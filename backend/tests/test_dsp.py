"""DSP primitives: FFT scaling, bands, levels, clipping, peaks, smoothing."""
import numpy as np
import pytest

from app.audio.ring_buffer import MultichannelRingBuffer
from app.dsp import levels as lv
from app.dsp.analyzer import MultiChannelAnalyzer, ema_alpha
from app.dsp.spectrum import (DEFAULT_BANDS, FFTEngine, LogGrid, background_db, band_levels, db10, detect_peaks,
                              spectral_stats)

FS, N = 48000, 4096


def sine(f, amp=0.5, n=N, fs=FS):
    return amp * np.sin(2 * np.pi * f * np.arange(n) / fs)


@pytest.fixture(scope="module")
def fft():
    return FFTEngine(FS, N)


def test_fft_sine_amplitude_scaling(fft):
    f = 1000.0 * fft.bin_hz / fft.bin_hz  # off-bin frequency is fine thanks to peak interpolation
    amp2 = fft.amp2(fft.mag2(sine(f, 0.5)))
    assert db10(amp2.max()) == pytest.approx(20 * np.log10(0.5), abs=1.5)  # scalloping <= 1.42 dB (Hann)


def test_power_parseval(fft):
    rng = np.random.default_rng(0)
    x = 0.1 * rng.standard_normal(N)
    p = fft.power(fft.mag2(x))
    assert p.sum() == pytest.approx(np.mean(x ** 2), rel=0.15)


def test_multichannel_fft_matches_single(fft):
    x = np.stack([sine(440), sine(2000, 0.1)], axis=1)
    m = fft.mag2(x)
    assert np.allclose(m[:, 1], fft.mag2(x[:, 1]))


def test_band_levels_place_energy_in_correct_band(fft):
    grid = LogGrid(fft.freqs, fft.bin_hz, points=120, fraction=3)
    p = fft.power(fft.mag2(sine(700, 0.5)))
    levels = band_levels(grid, p, DEFAULT_BANDS)
    idx = [i for i, (lo, hi) in enumerate(DEFAULT_BANDS) if lo <= 700 < hi][0]
    assert np.argmax(levels) == idx
    assert levels[idx] == pytest.approx(10 * np.log10(0.5 ** 2 / 2), abs=1.0)  # sine RMS power


def test_log_grid_mean_of_constant_is_constant(fft):
    grid = LogGrid(fft.freqs, fft.bin_hz, points=64)
    out = grid.mean(np.ones_like(fft.freqs))
    assert np.allclose(out[grid.centers > 100], 1.0, atol=1e-6)


def test_spectral_stats_tone_vs_noise(fft):
    tone = spectral_stats(fft.freqs, fft.power(fft.mag2(sine(1000))))
    noise = spectral_stats(fft.freqs, fft.power(fft.mag2(np.random.default_rng(1).standard_normal(N))))
    assert tone["centroid_hz"] == pytest.approx(1000, rel=0.05)
    assert noise["flatness"] > 0.4 > tone["flatness"]
    assert noise["rolloff_hz"] > 10000


def test_peak_detection_frequency_and_q(fft):
    x = sine(2810.0, 0.2) + 0.001 * np.random.default_rng(2).standard_normal(N)
    amp_db = db10(fft.amp2(fft.mag2(x)))
    grid3 = LogGrid(fft.freqs, fft.bin_hz, points=120, fraction=3)
    peaks = detect_peaks(fft.freqs, amp_db, background_db(grid3, amp_db, fft.freqs), fft.bin_hz)
    assert len(peaks) >= 1
    pk = max(peaks, key=lambda p: p.level_db)
    assert pk.freq == pytest.approx(2810.0, abs=fft.bin_hz / 4)
    assert pk.resolution_limited
    assert pk.prominence_db > 30


def test_levels_rms_peak_crest():
    x = sine(1000, 0.5, n=48000)[:, None]
    b = lv.measure_block(x)
    assert b.rms_dbfs[0] == pytest.approx(20 * np.log10(0.5 / np.sqrt(2)), abs=0.05)
    assert b.peak_dbfs[0] == pytest.approx(20 * np.log10(0.5), abs=0.05)
    assert b.crest_db[0] == pytest.approx(3.01, abs=0.05)
    assert b.clipped_samples[0] == 0


def test_clipping_detection_counts_runs():
    x = np.clip(sine(100, 2.0, n=4800), -1, 1)[:, None]
    b = lv.measure_block(x)
    assert b.clipped_samples[0] > 100
    assert b.max_clip_run[0] >= 3
    single = np.zeros((100, 1))
    single[50] = 1.0
    b2 = lv.measure_block(single)
    assert b2.clipped_samples[0] == 1 and b2.max_clip_run[0] == 1


def test_longest_run():
    assert lv.longest_run(np.array([0, 1, 1, 0, 1, 1, 1, 0], bool)) == 3
    assert lv.longest_run(np.zeros(5, bool)) == 0


def test_ema_alpha():
    assert ema_alpha(10, 0) == 1.0
    assert 0 < ema_alpha(10, 1.0) < 0.2


def test_analyzer_temporal_smoothing_and_spectrogram():
    mca = MultiChannelAnalyzer(FS, N, 10, spectrogram_history=20)
    hop = 4800
    x = sine(1000, 0.3, n=hop * 30)
    buf = np.zeros((N, 1))
    for i in range(30):
        blk = x[i * hop:(i + 1) * hop, None]
        buf = np.concatenate([buf, blk])[-N:]
        a = mca.process(blk, buf, [0])[0]
    an = mca.channels[0]
    assert len(an.spectrogram) == 20
    assert len(an.spectrogram[-1]) == 128
    k = int(np.argmax(a.display_db))
    assert mca.grid_display.centers[k] == pytest.approx(1000, rel=0.06)
    assert a.active and a.rms_dbfs == pytest.approx(20 * np.log10(0.3 / np.sqrt(2)), abs=0.1)
    assert any(abs(t["freq_hz"] - 1000) < 15 for t in a.tracks)


def test_analyzer_only_processes_requested_channels():
    mca = MultiChannelAnalyzer(FS, N, 10)
    x = np.zeros((N, 4))
    out = mca.process(x, x, [1, 3])
    assert set(out) == {1, 3} and set(mca.channels) == {1, 3}


# ------------------------------------------------------------------ ring buffer
def test_ring_buffer_wraps_and_reads_in_order():
    rb = MultichannelRingBuffer(10, 2)
    cur = 0
    data = np.arange(30, dtype=np.float32).reshape(15, 2)
    rb.write(data[:6])
    out, cur, lost = rb.read_since(cur)
    assert lost == 0 and np.array_equal(out, data[:6])
    rb.write(data[6:13])  # wraps
    out, cur, lost = rb.read_since(cur)
    assert lost == 0 and np.array_equal(out, data[6:13])


def test_ring_buffer_reports_lost_frames_when_reader_is_slow():
    rb = MultichannelRingBuffer(8, 1)
    rb.write(np.arange(20, dtype=np.float32)[:, None])
    out, cur, lost = rb.read_since(0)
    assert lost == 12 and out.shape == (8, 1) and out[-1, 0] == 19 and cur == 20


def test_ring_buffer_latest_pads_with_zeros():
    rb = MultichannelRingBuffer(16, 1)
    rb.write(np.ones((4, 1), np.float32))
    out = rb.latest(8)
    assert out.shape == (8, 1) and out[:4].sum() == 0 and out[4:].sum() == 4


def test_ring_buffer_ignores_extra_input_channels():
    rb = MultichannelRingBuffer(8, 2)
    rb.write(np.ones((4, 3), np.float32))
    assert rb.latest(4).shape == (4, 2)
