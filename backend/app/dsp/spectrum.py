"""Spectrum analysis primitives.

Scaling conventions (see DSP_DESIGN.md):

* ``power`` - one-sided per-bin power normalised so ``power.sum()`` equals the
  mean square of the input (Parseval with window correction). Band energies and
  spectral statistics use this, so a full-scale sine has total power 0.5
  (-3.0 dBFS RMS).
* ``amp2`` - per-bin squared *sine amplitude* estimate, i.e. a sine of
  amplitude A produces a peak of A^2 (0 dB for full scale). Used for display,
  peak detection and Q estimation.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks

EPS = 1e-20

DEFAULT_BANDS: list[tuple[float, float]] = [
    (20, 60), (60, 120), (120, 250), (250, 500), (500, 1000),
    (1000, 2000), (2000, 4000), (4000, 8000), (8000, 16000),
]


def db10(x: np.ndarray | float) -> np.ndarray:
    return 10.0 * np.log10(np.maximum(x, EPS))


def band_label(lo: float, hi: float) -> str:
    f = lambda v: f"{v / 1000:g}k" if v >= 1000 else f"{v:g}"  # noqa: E731
    return f"{f(lo)}-{f(hi)}"


class FFTEngine:
    """Windowed real FFT for one or many channels at a fixed size."""

    def __init__(self, fs: float, nfft: int):
        self.fs, self.nfft = float(fs), int(nfft)
        self.window = np.hanning(self.nfft + 1)[:-1].astype(np.float64)  # periodic Hann
        self.sum_w = self.window.sum()
        self.sum_w2 = (self.window ** 2).sum()
        self.freqs = np.fft.rfftfreq(self.nfft, 1.0 / self.fs)
        self.bin_hz = self.fs / self.nfft
        self._pscale = np.full(self.freqs.shape, 2.0 / (self.nfft * self.sum_w2))
        self._pscale[0] /= 2
        if self.nfft % 2 == 0:
            self._pscale[-1] /= 2
        self._ascale = (2.0 / self.sum_w) ** 2

    def mag2(self, x: np.ndarray) -> np.ndarray:
        """x: (nfft,) or (nfft, ch) -> |X|^2 with shape (bins,) or (bins, ch)."""
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            X = np.fft.rfft(x * self.window)
        else:
            X = np.fft.rfft(x * self.window[:, None], axis=0)
        return X.real ** 2 + X.imag ** 2

    def power(self, mag2: np.ndarray) -> np.ndarray:
        return mag2 * (self._pscale if mag2.ndim == 1 else self._pscale[:, None])

    def amp2(self, mag2: np.ndarray) -> np.ndarray:
        return mag2 * self._ascale


class LogGrid:
    """Log-spaced frequency grid with fractional-octave averaging.

    Averaging uses the cumulative sum of a per-bin quantity, linearly
    interpolated at band edges, so bands narrower than one FFT bin still get a
    sensible value and the cost is O(bins) per call.
    """

    def __init__(self, freqs: np.ndarray, bin_hz: float, points: int = 240, fmin: float = 20.0,
                 fmax: float | None = None, fraction: float = 6.0):
        nyq = freqs[-1]
        fmax = min(fmax or 20000.0, nyq * 0.98)
        self.centers = np.geomspace(fmin, fmax, points)
        half = 2 ** (1.0 / (2 * fraction))
        self.lo = self.centers / half
        self.hi = self.centers * half
        self.bin_hz = bin_hz
        # cumulative edges: bin k spans [(k-0.5) df, (k+0.5) df]
        self._edges = (np.arange(len(freqs) + 1) - 0.5) * bin_hz
        self._width_bins = np.maximum((self.hi - self.lo) / bin_hz, 1e-9)

    def _cum(self, arr: np.ndarray) -> np.ndarray:
        z = np.zeros((1,) + arr.shape[1:])
        return np.concatenate([z, np.cumsum(arr, axis=0)], axis=0)

    def band_sum(self, arr: np.ndarray, lo: np.ndarray | float, hi: np.ndarray | float) -> np.ndarray:
        c = self._cum(arr)
        if arr.ndim == 1:
            return np.interp(hi, self._edges, c) - np.interp(lo, self._edges, c)
        return np.stack([np.interp(hi, self._edges, c[:, j]) - np.interp(lo, self._edges, c[:, j])
                         for j in range(arr.shape[1])], axis=-1)

    def mean(self, arr: np.ndarray) -> np.ndarray:
        """Average of a per-bin quantity over each grid band (per-bin units)."""
        s = self.band_sum(arr, self.lo, self.hi)
        w = self._width_bins if arr.ndim == 1 else self._width_bins[:, None]
        return s / w

    def max_hold(self, arr_db: np.ndarray, freqs: np.ndarray) -> np.ndarray:
        """Max of a per-bin dB array within each band (nearest bin when the band is narrower than a bin)."""
        if getattr(self, "_mh_idx", None) is None or self._mh_n != len(freqs):
            n = len(freqs)
            lo_i = np.clip(np.searchsorted(freqs, self.lo), 0, n - 1)
            hi_i = np.clip(np.searchsorted(freqs, self.hi), 0, n - 1)
            nearest = np.clip(np.round(self.centers / self.bin_hz).astype(int), 0, n - 1)
            lo_i = np.where(hi_i > lo_i, lo_i, nearest)
            hi_i = np.where(hi_i > lo_i, hi_i, nearest)
            self._mh_idx = np.ravel(np.column_stack([lo_i, hi_i]))
            self._mh_n = n
        return np.maximum.reduceat(arr_db, self._mh_idx)[::2]


def band_levels(grid: LogGrid, power: np.ndarray, bands: list[tuple[float, float]]) -> np.ndarray:
    lo = np.array([b[0] for b in bands], float)
    hi = np.array([b[1] for b in bands], float)
    return db10(grid.band_sum(power, lo, hi))


def spectral_stats(freqs: np.ndarray, power: np.ndarray, fmin: float = 20.0, fmax: float = 20000.0) -> dict[str, float]:
    m = (freqs >= fmin) & (freqs <= fmax)
    f, p = freqs[m], power[m]
    total = p.sum()
    if total <= EPS:
        return {"centroid_hz": 0.0, "rolloff_hz": 0.0, "flatness": 0.0}
    centroid = float((f * p).sum() / total)
    cum = np.cumsum(p)
    rolloff = float(f[min(np.searchsorted(cum, 0.85 * total), len(f) - 1)])
    fm = (f >= 100) & (f <= 16000)
    pp = np.maximum(p[fm], EPS)
    flatness = float(np.exp(np.mean(np.log(pp))) / np.mean(pp)) if pp.size else 0.0
    return {"centroid_hz": centroid, "rolloff_hz": rolloff, "flatness": flatness}


@dataclass
class SpectralPeak:
    freq: float
    level_db: float       # sine-amplitude scale (dBFS)
    prominence_db: float  # above local (1/3-octave, dB-domain mean) background
    bandwidth_hz: float   # -3 dB width
    q: float
    resolution_limited: bool


def background_db(grid_third: LogGrid, amp_db: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Local spectral background: 1/3-octave mean of the *dB* spectrum
    (a geometric mean, so a single narrow peak barely raises it)."""
    smooth = grid_third.mean(amp_db)
    return np.interp(freqs, grid_third.centers, smooth)


def detect_peaks(freqs: np.ndarray, amp_db: np.ndarray, background: np.ndarray, bin_hz: float,
                 min_prominence_db: float = 10.0, floor_db: float = -100.0, fmin: float = 40.0,
                 fmax: float = 16000.0, max_peaks: int = 16) -> list[SpectralPeak]:
    idx, _ = find_peaks(amp_db)
    if idx.size == 0:
        return []
    prom = amp_db[idx] - background[idx]
    keep = (prom >= min_prominence_db) & (amp_db[idx] > floor_db) & (freqs[idx] >= fmin) & (freqs[idx] <= fmax)
    idx, prom = idx[keep], prom[keep]
    if idx.size > max_peaks:
        top = np.argsort(prom)[-max_peaks:]
        idx, prom = idx[top], prom[top]
    peaks: list[SpectralPeak] = []
    n = len(amp_db)
    for k, p in zip(idx, prom):
        # parabolic interpolation of the peak location
        if 0 < k < n - 1:
            a, b, c = amp_db[k - 1], amp_db[k], amp_db[k + 1]
            den = a - 2 * b + c
            delta = 0.5 * (a - c) / den if den != 0 else 0.0
            delta = float(np.clip(delta, -0.5, 0.5))
            level = b - 0.25 * (a - c) * delta
        else:
            delta, level = 0.0, amp_db[k]
        f = (k + delta) * bin_hz
        target = level - 3.0
        # walk to -3 dB points with linear interpolation
        left = k
        while left > 0 and amp_db[left] > target:
            left -= 1
        right = k
        while right < n - 1 and amp_db[right] > target:
            right += 1

        def cross(i0: int, i1: int) -> float:
            y0, y1 = amp_db[i0], amp_db[i1]
            if y1 == y0:
                return float(i0)
            return i0 + (target - y0) / (y1 - y0) * (i1 - i0)

        fl = cross(left, left + 1) if left < k else float(k)
        fr = cross(right - 1, right) if right > k else float(k)
        bw = max((fr - fl) * bin_hz, 1e-6)
        res_limited = bw <= 2.0 * bin_hz  # Hann main lobe: -3 dB width ~1.44 bins
        peaks.append(SpectralPeak(freq=float(f), level_db=float(level), prominence_db=float(p),
                                  bandwidth_hz=float(bw), q=float(f / bw), resolution_limited=bool(res_limited)))
    peaks.sort(key=lambda pk: pk.freq)
    return peaks
