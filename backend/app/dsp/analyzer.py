"""Per-channel continuous analysis with temporal smoothing.

Each analysis tick, for every *selected* channel:

1. All samples captured since the previous tick are measured for level,
   peak and clipping (nothing between FFT windows is missed).
2. The newest ``nfft`` samples are windowed and transformed (done for all
   channels at once by ``MultiChannelAnalyzer``).
3. Several exponential moving averages (EMAs) are updated:

   * ``display``  - fast (~0.25 s) log-grid spectrum for the UI.
   * ``long``     - slow (~1 s) per-bin power, *active frames only*: the tonal
                    balance that diagnostics are based on. Silence and noise
                    between phrases must not bias tonal balance.
   * ``noise``    - per-bin amplitude during *inactive* frames: the noise floor
                    spectrum (hum / hiss / broadband noise analysis).
   * ``hum``      - slow per-bin amplitude over all frames: stationary tones
                    (mains hum) survive the averaging, moving speech harmonics
                    do not.
4. Narrowband peaks are tracked over time (``PeakTracker``).

The result is a ``ChannelAnalysis`` snapshot consumed by the diagnostic rules
and (in compact form) by the UI. Nothing here makes judgements - it measures.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.dsp import levels as lv
from app.dsp.noise import HumResult, LevelHistory, detect_hum
from app.dsp.spectrum import (
    DEFAULT_BANDS, EPS, FFTEngine, LogGrid, background_db, band_label, band_levels, db10, detect_peaks,
    spectral_stats,
)
from app.dsp.tonal import PeakTracker

SPECTROGRAM_POINTS = 128
SPECTROGRAM_FLOOR_DB = -120.0


def ema_alpha(rate_hz: float, tc_s: float) -> float:
    if tc_s <= 0:
        return 1.0
    return 1.0 - math.exp(-1.0 / (rate_hz * tc_s))


@dataclass
class ChannelAnalysis:
    channel: int
    tick: int
    active: bool
    rms_dbfs: float
    peak_dbfs: float
    peak_hold_dbfs: float
    crest_db: float
    clipped_samples: int
    max_clip_run: int
    clip_ticks_recent: int
    clipped_samples_recent: int
    near_overload_ratio: float
    noise_floor_dbfs: float | None
    signal_dbfs: float | None
    snr_estimate_db: float | None
    activity_ratio: float
    active_frames: int
    inactive_frames: int
    grid_hz: np.ndarray
    long_db: np.ndarray            # 1/3-oct smoothed long-term power (active frames)
    noise_db: np.ndarray | None    # 1/3-oct smoothed noise spectrum (inactive frames)
    bands: list[tuple[float, float]]
    band_levels_long: np.ndarray
    band_levels_fast: np.ndarray
    stats: dict[str, float]
    noise_stats: dict[str, float] | None
    tracks: list[dict]
    hum: dict[str, HumResult]
    hum_noise: dict[str, HumResult] | None
    sibilance_p90_db: float | None
    sibilance_median_db: float | None
    display_db: np.ndarray
    maxhold_db: np.ndarray
    extras: dict[str, Any] = field(default_factory=dict)

    def band_dict(self, which: str = "fast") -> list[dict]:
        arr = self.band_levels_fast if which == "fast" else self.band_levels_long
        return [{"label": band_label(lo, hi), "lo": lo, "hi": hi, "level_db": round(float(v), 1)}
                for (lo, hi), v in zip(self.bands, arr)]


class ChannelAnalyzer:
    def __init__(self, channel: int, fft: FFTEngine, rate_hz: float, grid_display: LogGrid, grid_third: LogGrid,
                 grid_spectro: LogGrid, *, long_tc_s: float = 1.0, display_tc_s: float = 0.25,
                 gate_dbfs: float = -60.0, clip_threshold: float = 0.999, spectrogram_history: int = 120,
                 bands: list[tuple[float, float]] | None = None):
        self.channel = channel
        self.fft = fft
        self.rate_hz = rate_hz
        self.g_disp, self.g_third, self.g_spec = grid_display, grid_third, grid_spectro
        self.bands = bands or DEFAULT_BANDS
        self.clip_threshold = clip_threshold
        self.a_long = ema_alpha(rate_hz, long_tc_s)
        self.a_disp = ema_alpha(rate_hz, display_tc_s)
        self.a_hum = ema_alpha(rate_hz, 2.0)
        self.a_noise = ema_alpha(rate_hz, 1.5)
        self.history = LevelHistory(rate_hz, 20.0, gate_dbfs)
        self.tracker = PeakTracker(rate_hz, fft.bin_hz, window_s=4.0)
        nb = len(fft.freqs)
        self.long_power = np.zeros(nb)
        self.fast_power = np.zeros(nb)
        self.noise_amp2 = np.zeros(nb)
        self.hum_amp2 = np.zeros(nb)
        self.display_db = np.full(len(grid_display.centers), -140.0)
        self.active_frames = 0
        self.inactive_frames = 0
        self.tick = 0
        self.peak_hold = -240.0
        recent = max(5, int(rate_hz * 5))
        self.clip_hist: deque[tuple[int, int]] = deque(maxlen=recent)  # (clipped_samples, run)
        self.overload_hist: deque[bool] = deque(maxlen=recent)
        # per-frame 5-9 kHz energy and 200 Hz-2 kHz "body" energy (active frames)
        self.sib_hist: deque[float] = deque(maxlen=max(10, int(rate_hz * 8)))
        self.body_hist: deque[float] = deque(maxlen=max(10, int(rate_hz * 8)))
        self.spectrogram: deque[bytes] = deque(maxlen=spectrogram_history)
        self.last: ChannelAnalysis | None = None
        self._hum_cache = None
        self._slow_cache = None
        self._sib_cache: tuple[float, float] | None = None
        self._slow_every = max(1, int(round(rate_hz)))
        f = fft.freqs
        self._sib_band = (f >= 5000) & (f <= 9000)
        self._body_band = (f >= 200) & (f <= 2000)

    def reset(self) -> None:
        """Forget accumulated spectra (used for 're-analyze after adjustment')."""
        self.long_power[:] = 0
        self.fast_power[:] = 0
        self.noise_amp2[:] = 0
        self.hum_amp2[:] = 0
        self.active_frames = self.inactive_frames = 0
        self.tracker.reset()
        self.sib_hist.clear()
        self.body_hist.clear()
        self.clip_hist.clear()
        self.overload_hist.clear()
        self.peak_hold = -240.0
        self._hum_cache = None
        self._slow_cache = None
        self._sib_cache = None

    @staticmethod
    def _ema(state: np.ndarray, new: np.ndarray, alpha: float, first: bool) -> None:
        if first:
            state[:] = new
        else:
            state += alpha * (new - state)

    def process(self, new_samples: np.ndarray, mag2: np.ndarray, full: bool = True) -> ChannelAnalysis:
        """``full=False`` skips the slow-moving long-term statistics (long-term
        spectrum, band levels, spectral stats) and reuses the previous values;
        they only feed diagnostics, which are evaluated at a lower rate."""
        self.tick += 1
        fft = self.fft
        bl = lv.measure_block(new_samples, self.clip_threshold)
        rms_db = max(float(bl.rms_dbfs[0]), -120.0) if bl.frames else -120.0
        peak_db = max(float(bl.peak_dbfs[0]), -120.0) if bl.frames else -120.0
        active = self.history.push(rms_db)

        power = fft.power(mag2)
        amp2 = fft.amp2(mag2)
        amp_db = db10(amp2)

        # --- levels / clipping memory
        self.peak_hold = max(peak_db, self.peak_hold - 20.0 / self.rate_hz)  # 20 dB/s fall-back
        self.clip_hist.append((int(bl.clipped_samples[0]) if bl.frames else 0, int(bl.max_clip_run[0]) if bl.frames else 0))
        if active:
            self.overload_hist.append(peak_db > -1.0)

        # --- spectral EMAs
        self._ema(self.fast_power, power, self.a_disp, self.tick == 1)
        self._ema(self.hum_amp2, amp2, self.a_hum, self.tick == 1)
        if active:
            self._ema(self.long_power, power, self.a_long, self.active_frames == 0)
            self.active_frames += 1
            self.body_hist.append(float(db10(power[self._body_band].sum())))
            self.sib_hist.append(float(db10(power[self._sib_band].sum())))
        else:
            # Only frames close to the estimated floor describe the background noise;
            # quiet-but-not-silent frames (soft fricatives, reverb tails) are excluded.
            floor = self.history.noise_floor_dbfs
            if floor is None or rms_db <= floor + 3.0:
                self._ema(self.noise_amp2, amp2, self.a_noise, self.inactive_frames == 0)
                self.inactive_frames += 1

        disp_now = db10(self.g_disp.mean(amp2))
        if self.tick == 1:
            self.display_db[:] = disp_now
        else:
            self.display_db += self.a_disp * (disp_now - self.display_db)

        # --- peaks (raw frame) - only when there is signal above the gate
        if rms_db > self.history.gate:
            bg = background_db(self.g_third, amp_db, fft.freqs)
            peaks = detect_peaks(fft.freqs, amp_db, bg, fft.bin_hz)
        else:
            peaks = []
        self.tracker.update(peaks)

        # --- hum / noise-floor analysis: slow-moving, so evaluated about once per second
        noise_valid = self.inactive_frames >= self.rate_hz * 1.0
        if self._hum_cache is None or (self.tick + self.channel) % self._slow_every == 0:  # staggered per channel
            hum_db = db10(self.hum_amp2)
            hum = {str(m): detect_hum(fft.freqs, hum_db, fft.bin_hz, m) for m in (50.0, 60.0)}
            hum_noise = noise_db = noise_stats = None
            if noise_valid:
                n_db = db10(self.noise_amp2)
                hum_noise = {str(m): detect_hum(fft.freqs, n_db, fft.bin_hz, m) for m in (50.0, 60.0)}
                noise_db = db10(self.g_third.mean(self.noise_amp2))
                noise_stats = spectral_stats(fft.freqs, self.noise_amp2)
                hf = self.noise_amp2[(fft.freqs >= 4000) & (fft.freqs <= 16000)].sum()
                tot = self.noise_amp2[(fft.freqs >= 20)].sum()
                noise_stats["hf_share"] = float(hf / tot) if tot > EPS else 0.0
                noise_stats["level_dbfs"] = float(db10(fft.power(self.noise_amp2 / fft._ascale).sum()))
            self._hum_cache = (hum, hum_noise, noise_db, noise_stats)
        hum, hum_noise, noise_db, noise_stats = self._hum_cache

        # --- spectrogram column (uint8, 0..255 => -120..0 dB)
        col = db10(self.g_spec.mean(amp2))
        q = np.clip((col - SPECTROGRAM_FLOOR_DB) / -SPECTROGRAM_FLOOR_DB * 255.0, 0, 255).astype(np.uint8)
        self.spectrogram.append(q.tobytes())

        long_src = self.long_power if self.active_frames else self.fast_power
        # Sibilance: loudest 5-9 kHz bursts relative to the channel's *typical* body level
        # (median over active frames) - a stable reference, unlike same-frame ratios which
        # explode in frames that contain only a fricative. Refreshed about once per second.
        if len(self.sib_hist) >= 5 and (self._sib_cache is None or (self.tick + self.channel) % self._slow_every == 0):
            sib = np.fromiter(self.sib_hist, float) - float(np.median(np.fromiter(self.body_hist, float)))
            self._sib_cache = (float(np.percentile(sib, 95)), float(np.median(sib)))
        elif len(self.sib_hist) < 5:
            self._sib_cache = None
        sib_p95, sib_med = self._sib_cache if self._sib_cache else (None, None)
        clip_counts = [c for c, _ in self.clip_hist]
        if full or self._slow_cache is None:
            self._slow_cache = (db10(self.g_third.mean(long_src)),
                                band_levels(self.g_third, long_src, self.bands),
                                spectral_stats(fft.freqs, long_src))
        long_db, bands_long, stats = self._slow_cache
        snap = ChannelAnalysis(
            channel=self.channel, tick=self.tick, active=active,
            rms_dbfs=rms_db, peak_dbfs=peak_db, peak_hold_dbfs=self.peak_hold,
            crest_db=float(bl.crest_db[0]) if bl.frames else 0.0,
            clipped_samples=clip_counts[-1] if clip_counts else 0,
            max_clip_run=max((r for _, r in self.clip_hist), default=0),
            clip_ticks_recent=sum(1 for c in clip_counts if c > 0),
            clipped_samples_recent=int(sum(clip_counts)),
            near_overload_ratio=(sum(self.overload_hist) / len(self.overload_hist)) if self.overload_hist else 0.0,
            noise_floor_dbfs=self.history.noise_floor_dbfs, signal_dbfs=self.history.signal_dbfs,
            snr_estimate_db=self.history.snr_estimate_db(), activity_ratio=self.history.activity_ratio,
            active_frames=self.active_frames, inactive_frames=self.inactive_frames,
            grid_hz=self.g_third.centers, long_db=long_db, noise_db=noise_db,
            bands=self.bands,
            band_levels_long=bands_long,
            band_levels_fast=band_levels(self.g_third, self.fast_power, self.bands),
            stats=stats, noise_stats=noise_stats,
            tracks=self.tracker.summaries(), hum=hum, hum_noise=hum_noise,
            sibilance_p90_db=sib_p95,
            sibilance_median_db=sib_med,
            display_db=self.display_db.copy(),
            maxhold_db=self.g_disp.max_hold(amp_db, fft.freqs),
        )
        self.last = snap
        return snap


class MultiChannelAnalyzer:
    """Owns per-channel analyzers sharing one FFT engine and frequency grids."""

    def __init__(self, fs: float, nfft: int, rate_hz: float, *, display_points: int = 240,
                 long_tc_s: float = 1.0, display_tc_s: float = 0.25, gate_dbfs: float = -60.0,
                 clip_threshold: float = 0.999, spectrogram_history: int = 120,
                 bands: list[tuple[float, float]] | None = None):
        self.fs, self.nfft, self.rate_hz = fs, nfft, rate_hz
        self.fft = FFTEngine(fs, nfft)
        f, df = self.fft.freqs, self.fft.bin_hz
        self.grid_display = LogGrid(f, df, points=display_points, fraction=6)
        self.grid_third = LogGrid(f, df, points=120, fraction=3)
        self.grid_spectro = LogGrid(f, df, points=SPECTROGRAM_POINTS, fraction=6)
        self.kw = dict(long_tc_s=long_tc_s, display_tc_s=display_tc_s, gate_dbfs=gate_dbfs,
                       clip_threshold=clip_threshold, spectrogram_history=spectrogram_history, bands=bands)
        self.channels: dict[int, ChannelAnalyzer] = {}

    def analyzer(self, ch: int) -> ChannelAnalyzer:
        a = self.channels.get(ch)
        if a is None:
            a = ChannelAnalyzer(ch, self.fft, self.rate_hz, self.grid_display, self.grid_third, self.grid_spectro,
                                **self.kw)
            self.channels[ch] = a
        return a

    def drop_except(self, keep: set[int]) -> None:
        for ch in list(self.channels):
            if ch not in keep:
                del self.channels[ch]

    def process(self, new_block: np.ndarray, window: np.ndarray, channels: list[int],
                full: set[int] | None = None) -> dict[int, ChannelAnalysis]:
        """new_block: (frames, all_ch) since last tick; window: (nfft, all_ch) newest samples.
        ``full``: channels that need the slow statistics this tick (default: all)."""
        if not channels:
            return {}
        cols = np.asarray(channels)
        mag2 = self.fft.mag2(window[:, cols])
        out: dict[int, ChannelAnalysis] = {}
        for j, ch in enumerate(channels):
            out[ch] = self.analyzer(ch).process(new_block[:, ch], mag2[:, j], full is None or ch in full)
        return out
