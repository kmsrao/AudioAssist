"""Tracking of narrowband spectral peaks over time.

A single FFT frame cannot distinguish a feedback build-up from a sung note or
a transient. The tracker associates peaks across frames and accumulates:

* persistence  - fraction of recent frames in which the peak was present
* stability    - relative standard deviation of its frequency
* narrowness   - median Q / resolution-limited flag
* level trend  - slope of its level (dB/s); feedback tends to grow
* harmonicity  - whether it belongs to a harmonic series with other tracked
  peaks (typical of musical notes, atypical of single-frequency feedback)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from app.dsp.spectrum import SpectralPeak


@dataclass
class PeakTrack:
    track_id: int
    freq: float
    born_tick: int
    last_tick: int
    hits: deque = field(default_factory=lambda: deque(maxlen=60))
    freqs: deque = field(default_factory=lambda: deque(maxlen=60))
    levels: deque = field(default_factory=lambda: deque(maxlen=60))
    proms: deque = field(default_factory=lambda: deque(maxlen=60))
    qs: deque = field(default_factory=lambda: deque(maxlen=60))
    res_limited: deque = field(default_factory=lambda: deque(maxlen=60))
    harmonic: bool = False

    def summary(self, rate_hz: float) -> dict:
        # Pure-Python statistics: these deques are short (<= window), where
        # numpy's per-call overhead dominates.
        hits = list(self.hits)
        fr = list(self.freqs)
        lv = list(self.levels)
        n = len(fr)
        mean_f = sum(fr) / n if n else self.freq
        if n > 1:
            var = sum((x - mean_f) ** 2 for x in fr) / n
            stability = (var ** 0.5) / mean_f if mean_f else 1.0
        else:
            stability = 1.0
        trend = 0.0
        m = len(lv)
        if m >= 5:  # least-squares slope, dB per second
            tm = (m - 1) / 2.0
            lm = sum(lv) / m
            num = sum((i - tm) * (y - lm) for i, y in enumerate(lv))
            den = sum((i - tm) ** 2 for i in range(m))
            trend = num / den * rate_hz if den else 0.0
        recent = fr[-10:]
        return {
            "id": self.track_id,
            "freq_hz": round(sum(recent) / len(recent), 1) if recent else round(self.freq, 1),
            "level_db": round(lv[-1], 1) if lv else -200.0,
            "prominence_db": round(_median(self.proms), 1),
            "q": round(_median(self.qs), 1),
            "resolution_limited": (sum(self.res_limited) / len(self.res_limited) > 0.5) if self.res_limited else False,
            "persistence": round(sum(hits) / len(hits), 3) if hits else 0.0,
            "observed_s": round(len(hits) / rate_hz, 2),
            "freq_stability": round(stability, 5),
            "level_trend_db_per_s": round(trend, 2),
            "harmonic": self.harmonic,
        }


def _median(values) -> float:
    v = sorted(values)
    n = len(v)
    if n == 0:
        return 0.0
    return float(v[n // 2]) if n % 2 else float(0.5 * (v[n // 2 - 1] + v[n // 2]))


class PeakTracker:
    def __init__(self, rate_hz: float, bin_hz: float, window_s: float = 4.0, max_tracks: int = 48):
        self.rate_hz = rate_hz
        self.bin_hz = bin_hz
        self.window = max(5, int(window_s * rate_hz))
        self.max_tracks = max_tracks
        self.tracks: list[PeakTrack] = []
        self.tick = 0
        self._next_id = 1

    def _tol(self, f: float) -> float:
        return max(1.5 * self.bin_hz, 0.01 * f)

    def update(self, peaks: list[SpectralPeak]) -> None:
        self.tick += 1
        matched_peak: list[int | None] = [None] * len(self.tracks)
        taken = np.zeros(len(peaks), dtype=bool)
        if self.tracks and peaks:
            tf = np.array([t.freq for t in self.tracks])
            pf = np.array([p.freq for p in peaks])
            dist = np.abs(tf[:, None] - pf[None, :])
            tol = np.maximum(1.5 * self.bin_hz, 0.01 * tf)[:, None]
            dist = np.where(dist <= tol, dist, np.inf)
            # greedy: closest (track, peak) pairs first
            order = np.argsort(dist, axis=None)
            n_ok = int(np.isfinite(dist).sum())
            used_t = np.zeros(len(self.tracks), dtype=bool)
            for flat in order[:n_ok]:
                ti, pi = divmod(int(flat), len(peaks))
                if used_t[ti] or taken[pi]:
                    continue
                used_t[ti] = taken[pi] = True
                matched_peak[ti] = pi
        for tr, pi in zip(self.tracks, matched_peak):
            if pi is not None:
                best = peaks[pi]
                tr.hits.append(1.0)
                tr.freqs.append(best.freq)
                tr.levels.append(best.level_db)
                tr.proms.append(best.prominence_db)
                tr.qs.append(best.q)
                tr.res_limited.append(best.resolution_limited)
                tr.freq = 0.7 * tr.freq + 0.3 * best.freq
                tr.last_tick = self.tick
            else:
                tr.hits.append(0.0)
        unmatched = [p for p, t in zip(peaks, taken) if not t]
        for pk in unmatched:
            if len(self.tracks) >= self.max_tracks:
                break
            tr = PeakTrack(self._next_id, pk.freq, self.tick, self.tick,
                           hits=deque([1.0], maxlen=self.window), freqs=deque([pk.freq], maxlen=self.window),
                           levels=deque([pk.level_db], maxlen=self.window), proms=deque([pk.prominence_db], maxlen=self.window),
                           qs=deque([pk.q], maxlen=self.window), res_limited=deque([pk.resolution_limited], maxlen=self.window))
            self._next_id += 1
            self.tracks.append(tr)
        # drop tracks unseen for ~1 s (transient peaks die quickly; keeps the set small)
        grace = max(3, int(self.rate_hz * 1.0))
        self.tracks = [t for t in self.tracks if self.tick - t.last_tick < grace]
        self._mark_harmonics()

    _RATIOS = np.array([0.5, 1 / 3, 2.0, 3.0, 1.5, 2 / 3])
    _MAX_HARMONIC = 16

    def _mark_harmonics(self) -> None:
        """A track is 'harmonic' when other live tracks relate to it as a
        harmonic series: either >=2 simple ratios (1/2, 1/3, 2, 3, 3/2, 2/3) or
        >=3 integer relations (f/g or g/f close to an integer 2..16, within
        max(1.5 bins, 0.5 %) of the expected partial)."""
        for t in self.tracks:
            t.harmonic = False
        live = [t for t in self.tracks if self.tick - t.last_tick <= 2]
        if len(live) < 3:
            return
        fs = np.array([t.freq for t in live])
        targets = fs[:, None] * self._RATIOS[None, :]                      # (n, r)
        tol = np.maximum(1.5 * self.bin_hz, 0.01 * targets)
        close = np.abs(fs[None, None, :] - targets[:, :, None]) <= tol[:, :, None]  # (n, r, n)
        simple = close.any(axis=2).sum(axis=1)
        hi = np.maximum(fs[:, None], fs[None, :])
        lo = np.minimum(fs[:, None], fs[None, :])
        n_int = np.round(hi / lo)
        err = np.abs(hi - n_int * lo)
        int_tol = np.maximum(1.5 * self.bin_hz, 0.005 * hi)
        integer = (n_int >= 2) & (n_int <= self._MAX_HARMONIC) & (err <= int_tol)
        np.fill_diagonal(integer, False)
        related_int = integer.sum(axis=1)
        for t, r_s, r_i in zip(live, simple, related_int):
            t.harmonic = bool(r_s >= 2 or r_i >= 3)

    def summaries(self, min_observed_s: float = 0.5, min_persistence: float = 0.5) -> list[dict]:
        """Summaries of candidate tracks only (cheap pre-filter on raw deques)."""
        min_frames = int(min_observed_s * self.rate_hz)
        out = []
        for t in self.tracks:
            n = len(t.hits)
            if n < min_frames or sum(t.hits) / n < min_persistence:
                continue
            out.append(t.summary(self.rate_hz))
        return out

    def reset(self) -> None:
        self.tracks.clear()
        self.tick = 0
