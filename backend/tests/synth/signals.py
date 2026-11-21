"""Synthetic test signals for automated tests and the calibration script.

Every generator is *streaming*: ``render(n)`` returns the next ``n`` samples
and keeps phase / filter state between calls, so a long signal rendered in
small blocks is identical to the same signal rendered at once. All randomness
comes from a seeded ``numpy.random.Generator`` for reproducible tests.

NOTE: the biquad filters here are used only to *colour synthetic signals*
(e.g. a muddy test voice). Live Audio Assist never processes or modifies
real input audio.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import signal as sps


# --------------------------------------------------------------------------- filters
def peaking_eq(fs: float, f0: float, gain_db: float, q: float) -> tuple[np.ndarray, np.ndarray]:
    """RBJ cookbook peaking EQ biquad coefficients."""
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2 * q)
    b = np.array([1 + alpha * a, -2 * math.cos(w0), 1 - alpha * a])
    den = np.array([1 + alpha / a, -2 * math.cos(w0), 1 - alpha / a])
    return b / den[0], den / den[0]


class Generator:
    def render(self, n: int) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


class Filtered(Generator):
    """Stateful IIR filter applied to a source generator."""

    def __init__(self, src: Generator, b: np.ndarray, a: np.ndarray):
        self.src, self.b, self.a = src, np.asarray(b, float), np.asarray(a, float)
        self.zi = np.zeros(max(len(self.a), len(self.b)) - 1)

    def render(self, n: int) -> np.ndarray:
        y, self.zi = sps.lfilter(self.b, self.a, self.src.render(n), zi=self.zi)
        return y


class FilteredSOS(Generator):
    def __init__(self, src: Generator, sos: np.ndarray):
        self.src, self.sos = src, sos
        self.zi = np.zeros((sos.shape[0], 2))

    def render(self, n: int) -> np.ndarray:
        y, self.zi = sps.sosfilt(self.sos, self.src.render(n), zi=self.zi)
        return y


class Sum(Generator):
    def __init__(self, *parts: Generator):
        self.parts = parts

    def render(self, n: int) -> np.ndarray:
        out = np.zeros(n)
        for p in self.parts:
            out += p.render(n)
        return out


class Gain(Generator):
    def __init__(self, src: Generator, gain: float):
        self.src, self.gain = src, gain

    def render(self, n: int) -> np.ndarray:
        return self.src.render(n) * self.gain


class HardClip(Generator):
    """Simulates an overloaded preamp/ADC: drive then clamp at full scale."""

    def __init__(self, src: Generator, drive: float = 4.0, ceiling: float = 1.0):
        self.src, self.drive, self.ceiling = src, drive, ceiling

    def render(self, n: int) -> np.ndarray:
        return np.clip(self.src.render(n) * self.drive, -self.ceiling, self.ceiling)


# --------------------------------------------------------------------------- primitives
class Sine(Generator):
    def __init__(self, fs: float, freq: float, amp: float = 0.5, phase: float = 0.0,
                 amp_ramp_per_s: float = 0.0, max_amp: float = 1.0):
        self.fs, self.freq, self.amp, self.phase = fs, freq, amp, phase
        self.amp_ramp, self.max_amp = amp_ramp_per_s, max_amp

    def render(self, n: int) -> np.ndarray:
        idx = np.arange(n)
        ph = self.phase + 2 * math.pi * self.freq * idx / self.fs
        if self.amp_ramp:
            amps = np.minimum(self.amp + self.amp_ramp * idx / self.fs, self.max_amp)
            self.amp = float(min(self.amp + self.amp_ramp * n / self.fs, self.max_amp))
        else:
            amps = self.amp
        self.phase = float((self.phase + 2 * math.pi * self.freq * n / self.fs) % (2 * math.pi))
        return amps * np.sin(ph)


class MultiTone(Generator):
    def __init__(self, fs: float, freqs: list[float], amps: list[float] | float = 0.2):
        amps_l = amps if isinstance(amps, list) else [amps] * len(freqs)
        self.tones = [Sine(fs, f, a) for f, a in zip(freqs, amps_l)]

    def render(self, n: int) -> np.ndarray:
        return sum((t.render(n) for t in self.tones), np.zeros(n))


class WhiteNoise(Generator):
    def __init__(self, amp: float = 0.1, seed: int = 0):
        self.amp, self.rng = amp, np.random.default_rng(seed)

    def render(self, n: int) -> np.ndarray:
        return self.amp * self.rng.standard_normal(n)


class PinkNoise(Generator):
    """Pink (1/f) noise via Paul Kellet's economy filter on white noise."""

    def __init__(self, amp: float = 0.1, seed: int = 0):
        b = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
        a = np.array([1, -2.494956002, 2.017265875, -0.522189400])
        self._f = Filtered(WhiteNoise(1.0, seed), b, a)
        self.amp = amp
        self._norm = 1 / 0.106  # approx unity RMS scaling

    def render(self, n: int) -> np.ndarray:
        return self.amp * self._norm * self._f.render(n)


class Hum(Generator):
    """Mains hum: fundamental (50/60 Hz) plus decaying harmonics."""

    def __init__(self, fs: float, mains: float = 60.0, amp: float = 0.02, harmonics: int = 5):
        self._mt = MultiTone(fs, [mains * k for k in range(1, harmonics + 1)],
                             [amp / (k ** 0.8) for k in range(1, harmonics + 1)])

    def render(self, n: int) -> np.ndarray:
        return self._mt.render(n)


class Rumble(Generator):
    """Sub-audio / low-frequency rumble (handling noise, HVAC, stage vibration)."""

    def __init__(self, fs: float, amp: float = 0.1, cutoff: float = 45.0, seed: int = 1):
        sos = sps.butter(4, cutoff, btype="low", fs=fs, output="sos")
        self._f = FilteredSOS(WhiteNoise(1.0, seed), sos)
        self.amp = amp
        self._norm = 1 / math.sqrt(cutoff / (fs / 2))

    def render(self, n: int) -> np.ndarray:
        return self.amp * self._norm * self._f.render(n)


# --------------------------------------------------------------------------- speech-like
_VOWELS = [  # (F1, F2, F3) Hz, rough adult averages
    (730, 1090, 2440), (270, 2290, 3010), (300, 870, 2240), (530, 1840, 2480),
    (570, 840, 2410), (440, 1020, 2240), (660, 1720, 2410), (490, 1350, 1690),
]


class SpeechLike(Generator):
    """Speech-like signal: glottal pulse train with prosodic pitch movement,
    time-varying vowel formants, fricative noise bursts and syllabic / phrase
    envelopes with pauses. Not intelligible speech - but it has the spectral
    and temporal statistics (varying f0, formant structure, pauses) that
    matter for the analyzer."""

    def __init__(self, fs: float, amp: float = 0.2, f0: float = 120.0, seed: int = 2,
                 sustained: bool = False, sibilance: float = 0.15, pause_prob: float = 0.15,
                 fric_gain: float = 0.06, direct: float = 0.1, formant_gains: tuple = (0.7, 0.6, 0.4, 0.5)):
        self.fs, self.amp, self.f0, self.sustained = fs, amp, f0, sustained
        self.rng = np.random.default_rng(seed)
        self.sibilance, self.pause_prob, self.fric_gain = sibilance, pause_prob, fric_gain
        self.direct, self.formant_gains = direct, formant_gains
        self.t = 0
        self._phase = 0.0
        self._syl_left = 0
        self._syl_len = 1
        self._syl_kind = "voiced"
        self._f0_target = f0
        self._f0_cur = f0
        # glottal source shaping (~ -6 dB/oct above ~400 Hz incl. radiation)
        self._src_b, self._src_a = sps.butter(1, 400, btype="low", fs=fs)
        self._src_zi = np.zeros(1)
        self._formants: list[Filtered] = []
        self._noise = WhiteNoise(1.0, seed + 100)
        self._fric_sos = sps.butter(4, [3000, 8000], btype="band", fs=fs, output="sos")
        self._fric_zi = np.zeros((self._fric_sos.shape[0], 2))
        self._form_zi = [np.zeros(2) for _ in range(4)]
        self._form_coefs: list[tuple[np.ndarray, np.ndarray]] = []
        self._new_syllable()

    def _new_syllable(self) -> None:
        r = self.rng.random()
        if r < self.pause_prob:
            self._syl_kind = "pause"
            dur = self.rng.uniform(0.25, 0.7)
        elif r < self.pause_prob + self.sibilance:
            self._syl_kind = "fricative"
            dur = self.rng.uniform(0.06, 0.14)
        else:
            self._syl_kind = "voiced"
            dur = self.rng.uniform(0.35, 0.9) if self.sustained else self.rng.uniform(0.12, 0.3)
            f1, f2, f3 = _VOWELS[self.rng.integers(len(_VOWELS))]
            self._form_coefs = [
                sps.iirpeak(min(f, self.fs / 2 - 100), q, fs=self.fs)
                for f, q in ((f1, 5), (f2, 8), (f3, 10), (3500, 6))
            ]
            self._f0_target = self.f0 * self.rng.uniform(0.85, 1.25)
        self._syl_len = self._syl_left = max(1, int(dur * self.fs))

    def _render_segment(self, n: int) -> np.ndarray:
        pos = self._syl_len - self._syl_left
        idx = np.arange(pos, pos + n)
        x = idx / self._syl_len
        if self._syl_kind == "pause":
            return np.zeros(n)
        env = np.sin(np.pi * np.clip(x, 0, 1)) ** (0.6 if self.sustained else 1.0)
        if self._syl_kind == "fricative":
            y, self._fric_zi = sps.sosfilt(self._fric_sos, self._noise.render(n), zi=self._fric_zi)
            return self.fric_gain * y * env
        # voiced: glide f0 toward target with vibrato for sustained singing
        f0 = self._f0_cur + (self._f0_target - self._f0_cur) * np.clip(x * 3, 0, 1)
        if self.sustained:
            f0 = f0 * (1 + 0.01 * np.sin(2 * np.pi * 5.5 * idx / self.fs))
        ph = self._phase + np.cumsum(f0 / self.fs)
        pulses = np.diff(np.floor(np.concatenate([[self._phase], ph])))
        self._phase = float(ph[-1] % 1.0)
        src, self._src_zi = sps.lfilter(self._src_b, self._src_a, pulses * 8.0, zi=self._src_zi)
        src += 0.02 * self._noise.render(n)  # breathiness
        y = np.zeros(n)
        for i, (b, a) in enumerate(self._form_coefs):
            seg, self._form_zi[i] = sps.lfilter(b, a, src, zi=self._form_zi[i])
            y += seg * self.formant_gains[i]
        y += self.direct * src  # some direct source energy (low harmonics)
        if self._syl_left <= n:
            self._f0_cur = float(self._f0_target)
        return y * env

    def render(self, n: int) -> np.ndarray:
        out = np.empty(n)
        done = 0
        while done < n:
            take = min(n - done, self._syl_left)
            out[done : done + take] = self._render_segment(take)
            self._syl_left -= take
            done += take
            if self._syl_left <= 0:
                self._new_syllable()
        self.t += n
        return self.amp * out


# --------------------------------------------------------------------------- instruments
@dataclass
class _Note:
    start: int
    f0: float
    amp: float
    decay: float


class Plucked(Generator):
    """Additive plucked/struck harmonic instrument (guitar, keys, bass)."""

    def __init__(self, fs: float, notes_hz: list[float], amp: float = 0.2, interval_s: float = 0.6,
                 decay_s: float = 1.2, harmonics: int = 8, rolloff: float = 1.0, seed: int = 3,
                 chord: int = 1):
        self.fs, self.notes_hz, self.amp = fs, notes_hz, amp
        self.interval = int(interval_s * fs)
        self.decay, self.harm, self.rolloff, self.chord = decay_s, harmonics, rolloff, chord
        self.rng = np.random.default_rng(seed)
        self.t = 0
        self.next_onset = 0
        self.active: list[tuple[_Note, np.ndarray]] = []

    def _note_wave(self, note: _Note) -> np.ndarray:
        rate0 = 1.0 / note.decay
        length = int(4.6 / rate0 * self.fs)  # until ~-40 dB
        sig = np.zeros(length, dtype=np.float32)
        for k in range(1, self.harm + 1):
            fk = note.f0 * k
            if fk > self.fs / 2 - 500:
                break
            rate = rate0 + 0.4 * k  # higher partials decay faster
            nk = min(length, int(4.6 / rate * self.fs))
            tt = np.arange(nk, dtype=np.float32) / self.fs
            sig[:nk] += np.sin(2 * np.pi * fk * tt) * np.exp(-tt * rate) * (1 / k ** self.rolloff)
        return note.amp * sig

    def render(self, n: int) -> np.ndarray:
        end = self.t + n
        while self.next_onset < end:
            for _ in range(self.chord):
                f = float(self.rng.choice(self.notes_hz))
                note = _Note(self.next_onset, f, self.amp * self.rng.uniform(0.7, 1.0), self.decay)
                self.active.append((note, self._note_wave(note)))
            self.next_onset += int(self.interval * self.rng.uniform(0.8, 1.2))
        out = np.zeros(n)
        keep = []
        for note, wave in self.active:
            lo, hi = max(self.t, note.start), min(end, note.start + len(wave))
            if hi > lo:
                out[lo - self.t : hi - self.t] += wave[lo - note.start : hi - note.start]
            if note.start + len(wave) > end:
                keep.append((note, wave))
        self.active = keep
        self.t = end
        return out


class Percussive(Generator):
    """Kick / snare style events."""

    def __init__(self, fs: float, kind: str = "kick", amp: float = 0.6, interval_s: float = 0.5, seed: int = 4):
        self.fs, self.kind, self.amp = fs, kind, amp
        self.interval = int(interval_s * fs)
        self.t = 0
        self.rng = np.random.default_rng(seed)
        self.onsets: list[int] = [0]
        self._noise_sos = sps.butter(2, [900, 9000], btype="band", fs=fs, output="sos")
        self._cache: dict[int, np.ndarray] = {}

    def _event(self, tt: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        if self.kind == "kick":
            f = 50 + 100 * np.exp(-tt / 0.03)
            ph = 2 * np.pi * np.cumsum(f) / self.fs
            body = np.sin(ph) * np.exp(-tt / 0.18)
            click = np.exp(-tt / 0.003) * rng.standard_normal(tt.shape) * 0.3
            return body + click
        # snare: tonal body + bright noise
        body = np.sin(2 * np.pi * 190 * tt) * np.exp(-tt / 0.06)
        noise = sps.sosfilt(self._noise_sos, rng.standard_normal(tt.shape)) * np.exp(-tt / 0.12)
        return 0.6 * body + 1.2 * noise

    def render(self, n: int) -> np.ndarray:
        end = self.t + n
        while self.onsets[-1] + self.interval < end:
            self.onsets.append(self.onsets[-1] + self.interval)
        out = np.zeros(n)
        horizon = int(0.8 * self.fs)
        for onset in self.onsets:
            lo, hi = max(self.t, onset), min(end, onset + horizon)
            if hi <= lo:
                continue
            ev = self._cache.get(onset)
            if ev is None:  # rendered once per event, deterministic per onset
                ev = self._event(np.arange(horizon) / self.fs, np.random.default_rng(onset))
                self._cache[onset] = ev
            out[lo - self.t : hi - self.t] += ev[lo - onset : hi - onset]
        self.onsets = [o for o in self.onsets if o + horizon > end] or [self.onsets[-1]]
        self._cache = {o: self._cache[o] for o in self.onsets if o in self._cache}
        self.t = end
        return self.amp * out


class Silence(Generator):
    def render(self, n: int) -> np.ndarray:
        return np.zeros(n)


# --------------------------------------------------------------------------- one-shot helpers for tests
def render(gen: Generator, seconds: float, fs: float, block: int = 512) -> np.ndarray:
    """Render ``seconds`` of a generator in ``block``-sized chunks (exercises streaming state)."""
    total = int(seconds * fs)
    out = np.empty(total)
    pos = 0
    while pos < total:
        n = min(block, total - pos)
        out[pos : pos + n] = gen.render(n)
        pos += n
    return out.astype(np.float32)


def speech(fs: float, seconds: float, seed: int = 2, amp: float = 0.25) -> np.ndarray:
    return render(SpeechLike(fs, amp=amp, seed=seed), seconds, fs)


def colored(gen: Generator, fs: float, f0: float, gain_db: float, q: float = 1.0) -> Generator:
    b, a = peaking_eq(fs, f0, gain_db, q)
    return Filtered(gen, b, a)
