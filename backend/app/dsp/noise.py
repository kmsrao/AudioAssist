"""Activity detection, noise-floor estimation and hum detection."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


class LevelHistory:
    """Rolling history of per-tick RMS levels for one channel.

    The noise floor is estimated as a low percentile of recent levels; the
    signal level as a high percentile. Their difference is only reported as an
    SNR *estimate* when the distribution actually shows separate quiet and
    active periods (spread >= ``min_spread_db``). Continuous material without
    pauses gives no defensible noise reference, so no SNR is reported then.
    """

    def __init__(self, rate_hz: float, seconds: float = 20.0, gate_dbfs: float = -60.0):
        self.levels: deque[float] = deque(maxlen=max(10, int(rate_hz * seconds)))
        self.gate = gate_dbfs
        self.active_flags: deque[bool] = deque(maxlen=self.levels.maxlen)

    FLOOR_DBFS = -120.0  # digital silence is clamped here so statistics stay meaningful

    PERCENTILE_EVERY = 5  # percentiles of a 20 s history move slowly; refresh every 5 pushes

    def push(self, rms_dbfs: float) -> bool:
        self.levels.append(max(float(rms_dbfs), self.FLOOR_DBFS))
        self._n += 1
        if len(self.levels) >= 10 and (self._p10 is None or self._n % self.PERCENTILE_EVERY == 0):
            v = sorted(self.levels)
            k = len(v) - 1
            self._p10, self._p90 = v[int(0.1 * k)], v[int(0.9 * k)]
        active = self.is_active(rms_dbfs)
        self.active_flags.append(active)
        return active

    _p10: float | None = None
    _p90: float | None = None
    _n: int = 0

    @property
    def noise_floor_dbfs(self) -> float | None:
        return self._p10

    @property
    def signal_dbfs(self) -> float | None:
        return self._p90

    @property
    def spread_db(self) -> float | None:
        lo, hi = self._p10, self._p90
        return None if lo is None or hi is None else hi - lo

    def is_active(self, rms_dbfs: float) -> bool:
        if rms_dbfs < self.gate:
            return False
        floor = self.noise_floor_dbfs
        spread = self.spread_db
        if floor is None or spread is None:
            return True
        if spread < 6.0:  # steady material (pad, drone) or steady noise above the gate
            return True
        return rms_dbfs > floor + 6.0

    def snr_estimate_db(self, min_spread_db: float = 10.0) -> float | None:
        spread = self.spread_db
        if spread is None or spread < min_spread_db:
            return None
        return spread

    @property
    def activity_ratio(self) -> float:
        if not self.active_flags:
            return 0.0
        return sum(self.active_flags) / len(self.active_flags)


@dataclass
class HumResult:
    mains_hz: float
    harmonics_found: int
    prominences_db: list[float]
    fundamental_level_db: float

    @property
    def score(self) -> float:
        if self.harmonics_found == 0:
            return 0.0
        return float(np.mean(sorted(self.prominences_db, reverse=True)[: max(2, self.harmonics_found)]))


def detect_hum(freqs: np.ndarray, amp_db: np.ndarray, bin_hz: float, mains: float,
               harmonics: int = 6, min_prominence_db: float = 10.0) -> HumResult:
    """Measure the prominence of mains harmonics.

    For each harmonic k*mains:

    * locate the local maximum within +/-1 bin and refine it by parabolic
      interpolation; it must lie within half a bin (+0.5 Hz) of k*mains -
      instruments near (but not at) mains harmonics are rejected;
    * compare it with the *higher* of the left/right neighbourhood medians
      (+/-1/3 octave, at least 8 bins, excluding +/-2 bins) so a steep spectral
      slope (e.g. rumble roll-off) cannot fake prominence.
    """
    proms: list[float] = []
    found = 0
    fund_level = -200.0
    k12 = False
    n = len(freqs)
    for k in range(1, harmonics + 1):
        f = mains * k
        c = int(round(f / bin_hz))
        if c + 10 >= n or c < 2:
            break
        i = c - 1 + int(np.argmax(amp_db[c - 1 : c + 2]))
        a_, b_, c_ = amp_db[i - 1], amp_db[i], amp_db[i + 1]
        den = a_ - 2 * b_ + c_
        delta = float(np.clip(0.5 * (a_ - c_) / den, -0.5, 0.5)) if den != 0 else 0.0
        f_est = (i + delta) * bin_hz
        lo = max(1, min(int(f / 2 ** (1 / 3) / bin_hz), c - 8))
        hi = min(n - 1, max(int(f * 2 ** (1 / 3) / bin_hz) + 1, c + 9))
        left, right = amp_db[lo : max(lo, c - 2)], amp_db[c + 3 : hi]
        refs = [float(np.median(x)) for x in (left, right) if x.size >= 2]
        if not refs:
            continue
        lvl = float(b_)
        prom = lvl - max(refs)
        on_freq = abs(f_est - f) <= 0.5 * bin_hz + 0.5
        if not on_freq:
            prom = min(prom, 0.0)
        proms.append(prom)
        if k == 1:
            fund_level = lvl
        if prom >= min_prominence_db:
            found += 1
            if k <= 2:
                k12 = True
    if not k12:
        found = min(found, 1)  # hum always shows its fundamental or 2nd harmonic
    return HumResult(mains, found, proms, fund_level)
