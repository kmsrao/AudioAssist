"""Synthetic signal generators, hum detection, level history and peak tracking."""
import numpy as np
import pytest

from tests.synth import signals as ms
from tests.synth.scenarios import SCENARIOS, build_channel_generators
from tests.synth.source import SyntheticSource
from app.dsp.noise import LevelHistory, detect_hum
from app.dsp.spectrum import FFTEngine, SpectralPeak, db10
from app.dsp.tonal import PeakTracker

FS = 48000


def test_streaming_render_matches_single_render():
    a = ms.render(ms.PinkNoise(0.1, seed=3), 1.0, FS, block=256)
    b = ms.render(ms.PinkNoise(0.1, seed=3), 1.0, FS, block=48000)
    assert np.allclose(a, b, atol=1e-6)
    s1 = ms.render(ms.Sine(FS, 440, 0.5), 0.5, FS, block=100)
    s2 = 0.5 * np.sin(2 * np.pi * 440 * np.arange(24000) / FS)
    assert np.allclose(s1, s2, atol=1e-4)


def test_generators_are_deterministic():
    assert np.array_equal(ms.speech(FS, 1.0, seed=7), ms.speech(FS, 1.0, seed=7))
    assert not np.array_equal(ms.speech(FS, 1.0, seed=7), ms.speech(FS, 1.0, seed=8))


def test_white_and_pink_noise_spectral_tilt():
    eng = FFTEngine(FS, 8192)
    for gen, expect_tilt in ((ms.WhiteNoise(0.1, 1), 0.0), (ms.PinkNoise(0.1, 1), -3.0)):
        x = ms.render(gen, 4.0, FS)
        p = np.mean([eng.power(eng.mag2(x[i:i + 8192])) for i in range(0, len(x) - 8192, 8192)], axis=0)
        f = eng.freqs
        lo = p[(f > 500) & (f < 1000)].mean()
        hi = p[(f > 4000) & (f < 8000)].mean()
        tilt_per_oct = db10(hi / lo) / 3.0  # 3 octaves apart
        assert tilt_per_oct == pytest.approx(expect_tilt, abs=1.0)


def test_clipping_generator_hits_full_scale():
    x = ms.render(ms.HardClip(ms.Sine(FS, 100, 0.8), drive=3), 0.2, FS)
    assert np.max(np.abs(x)) == pytest.approx(1.0)


def test_rumble_energy_is_low_frequency():
    eng = FFTEngine(FS, 8192)
    x = ms.render(ms.Rumble(FS, 0.1, 40.0), 1.0, FS)[:8192]
    p = eng.power(eng.mag2(x))
    assert p[eng.freqs < 80].sum() / p.sum() > 0.95


def test_scenarios_build_all_channels():
    for name in SCENARIOS:
        gens = build_channel_generators(name, FS, 10)
        assert len(gens) == 10
        assert all(g.render(256).shape == (256,) for g in gens)


def test_synthetic_source_pump_writes_ring():
    src = SyntheticSource(FS, 512, 4, "demo", realtime=False)
    src.start()
    src.pump(10)
    assert src.ring.total_written == 5120 and src.stats.callbacks == 10 and src.running
    src.stop()
    assert not src.running


def test_synthetic_source_realtime_thread_and_loop_cache():
    src = SyntheticSource(FS, 512, 2, "clean", realtime=True)
    src._loop = np.zeros((4096, 2), np.float32)  # tiny loop to exercise playback path
    src.start()
    import time
    time.sleep(0.4)
    src.stop()
    assert src.ring.total_written > FS * 0.2
    assert src._loop_filled == 4096


# ------------------------------------------------------------------ hum
def _hum_spectrum(x, nfft=8192):
    eng = FFTEngine(FS, nfft)
    amp = np.mean([eng.amp2(eng.mag2(x[i:i + nfft])) for i in range(0, len(x) - nfft, nfft // 2)], axis=0)
    return eng, db10(amp)


def test_hum_detected_at_correct_mains_frequency():
    x = ms.render(ms.Sum(ms.Hum(FS, 60.0, 0.01), ms.PinkNoise(0.001, 2)), 3.0, FS)
    eng, d = _hum_spectrum(x)
    h60 = detect_hum(eng.freqs, d, eng.bin_hz, 60.0)
    h50 = detect_hum(eng.freqs, d, eng.bin_hz, 50.0)
    assert h60.harmonics_found >= 3
    assert h50.harmonics_found <= 1


def test_hum_not_detected_on_noise_or_speech():
    for x in (ms.render(ms.PinkNoise(0.05, 4), 3.0, FS), ms.speech(FS, 3.0, seed=4, amp=0.4)):
        eng, d = _hum_spectrum(x)
        for mains in (50.0, 60.0):
            assert detect_hum(eng.freqs, d, eng.bin_hz, mains).harmonics_found < 3


# ------------------------------------------------------------------ level history
def test_level_history_noise_floor_and_snr():
    h = LevelHistory(10, 20, gate_dbfs=-60)
    rng = np.random.default_rng(0)
    for i in range(200):
        h.push(-25 + rng.normal(0, 1) if (i // 10) % 2 else -58 + rng.normal(0, 1))
    assert h.noise_floor_dbfs == pytest.approx(-58, abs=2.5)
    assert h.signal_dbfs == pytest.approx(-25, abs=2.5)
    assert h.snr_estimate_db() == pytest.approx(33, abs=4)
    assert h.is_active(-25) and not h.is_active(-58)


def test_level_history_no_snr_for_continuous_material():
    h = LevelHistory(10, 20)
    for _ in range(100):
        h.push(-20.0)
    assert h.snr_estimate_db() is None
    assert h.is_active(-20.0)


# ------------------------------------------------------------------ peak tracker
def _pk(f, level=-20.0, prom=25.0, q=100.0):
    return SpectralPeak(f, level, prom, f / q, q, True)


def test_tracker_persistence_and_transients():
    tr = PeakTracker(10, 11.7)
    for i in range(40):
        peaks = [_pk(2810.0 + np.sin(i) * 0.5)]
        if i == 5:
            peaks.append(_pk(5000.0))  # one-frame transient
        tr.update(peaks)
    s = {round(t["freq_hz"]): t for t in tr.summaries(min_observed_s=0.0, min_persistence=0.0)}
    steady = [t for f, t in s.items() if abs(f - 2810) < 5][0]
    assert steady["persistence"] == 1.0 and steady["observed_s"] >= 3.9
    assert steady["freq_stability"] < 0.001
    assert not any(abs(f - 5000) < 50 for f in s)  # transient track died


def test_tracker_marks_harmonic_series():
    tr = PeakTracker(10, 11.7)
    for _ in range(10):
        tr.update([_pk(220.0), _pk(440.0), _pk(660.0), _pk(880.0), _pk(3000.0)])
    by_f = {round(t.freq): t.harmonic for t in tr.tracks}
    assert by_f[440] and by_f[660]
    assert not by_f[3000]
