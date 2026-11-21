# DSP design

All signal processing runs in the backend (`backend/app/dsp`). The browser only draws.

## Frames, windows and rates

| Item | Default | Configurable |
|---|---|---|
| Sample rate | 48 kHz | 44.1 / 48 / 88.2 / 96 kHz |
| Capture block | 512 frames (10.7 ms) | 128 – 2048 |
| FFT size | 4096 (85 ms, 11.7 Hz bins) | 2048 / 4096 / 8192 |
| Analysis rate | 10 Hz | 2 – 30 Hz |
| Diagnostics rate | analysis rate ÷ 2 per channel (staggered) | – |
| UI rate | 10 Hz | 1 – 30 Hz (per client) |

Each tick the analyser reads **every** sample captured since the previous tick for level and
clipping, and the **newest FFT-size window** for spectral analysis. Consecutive windows overlap
when `fft_size / sample_rate > 1 / analysis_rate`.

## Spectrum

- **Window:** periodic Hann.
- **Power** (`FFTEngine.power`): one-sided, scaled `2|X|² / (N Σw²)` so that `Σ power = mean(x²)`
  (Parseval with window correction). Band energies and spectral statistics use this. A full-scale
  sine has total power 0.5 → **−3.0 dBFS RMS**.
- **Amplitude²** (`FFTEngine.amp2`): `(2|X| / Σw)²`, i.e. a sine of amplitude A peaks at A²
  (0 dBFS for full scale). Used for the displayed spectrum, peak detection and Q. Hann
  scalloping is ≤ 1.42 dB; peak levels/frequencies are refined with parabolic interpolation.
- **Log-frequency grids** (`LogGrid`): 240 points from 20 Hz (display, 1/6-octave averaging),
  120 points (analysis, 1/3-octave), 128 points (spectrogram). Averages use cumulative sums
  interpolated at band edges, so bands narrower than a bin still behave.

### Temporal smoothing

Diagnostics are never based on a single frame. Exponential moving averages (EMAs):

| EMA | Time constant | Updated on | Used by |
|---|---|---|---|
| display | 0.25 s | every frame | UI spectrum |
| long-term | 1 s (setting) | **active** frames only | tonal rules, long-term bands |
| noise | 1.5 s | frames within 3 dB of the estimated floor | broadband noise, hiss, rumble (noise), hum (pauses) |
| hum | 2 s | every frame | hum (moving speech harmonics average out; mains tones don't) |

### Interpreting the spectrum panel

- **Blue area** — smoothed level per 1/6 octave (dBFS, sine-amplitude scale). Broad shapes
  (tilt, bumps) are tonal balance.
- **Grey line** — instantaneous max within each 1/6-octave band: transient detail and narrow
  peaks before smoothing.
- **Yellow dots** — *persistent* narrow peaks from the tracker (≥ 70 % persistence over ≥ 1 s).
  Steady tones, sustained notes, ringing.
- **Dashed lines** — targets of active recommendations on this channel.
- The spectrogram shows the last `spectrogram_history` analysis frames (default 120 → 12 s);
  bright horizontal lines are steady tones (hum, ringing), vertical stripes are transients.
- **Band energy** — 9 analytical bands (20–60, 60–120, 120–250, 250–500, 500–1k, 1–2k, 2–4k,
  4–8k, 8–16k Hz). "Live" uses the 0.25 s EMA; "Long-term" is what diagnostics use. These are
  measurement bands, not EQ suggestions. Highlighted bars contain a recommendation target.

## Levels and dynamics (`dsp/levels.py`)

- **RMS / peak** in dBFS over all new samples (per channel), floor-clamped at −120 dBFS.
- **Peak hold** falls back at 20 dB/s.
- **Crest factor** = peak − RMS (dB).
- **Clipping:** samples with |x| ≥ `clip_threshold` (default 0.999) and the longest run of
  consecutive such samples. A single sample touching full scale is not treated as clipping;
  the rule requires runs ≥ 3 samples in ≥ 2 analysis periods within 5 s.
- **Near-overload:** fraction of active periods with peaks above −1 dBFS.

## Activity, noise floor and SNR (`dsp/noise.py`)

- `LevelHistory` keeps 20 s of per-tick RMS. Noise floor ≈ 10th percentile, typical signal level
  ≈ 90th percentile (refreshed every 5 ticks).
- **Active** = above the activity gate (−60 dBFS) and either > floor + 6 dB, or the material is
  steady (p90 − p10 < 6 dB, e.g. a pad).
- **SNR estimate** = p90 − p10, **only** reported when the spread is ≥ 10 dB (i.e. distinct
  quiet and active periods exist). Continuous material gives no defensible reference, so no
  number is shown. It is an estimate, not a calibrated measurement.

## Hum detection

For mains 50 and 60 Hz (or the configured one), harmonics k = 1…6 are checked on the hum EMA
and on the pause-only noise spectrum:

- local maximum within ±1 bin, parabolically refined, must lie within ½ bin + 0.5 Hz of k·mains;
- prominence = level − **max**(median left neighbourhood, median right neighbourhood), each at least
  8 bins wide — so a steep roll-off cannot fake a peak;
- a harmonic counts at ≥ 10 dB prominence; hum is reported with ≥ 3 harmonics, or a clear
  fundamental (≥ 15 dB) plus 2nd harmonic (≥ 10 dB). The fundamental or 2nd harmonic must be
  among the counted ones.

At FFT 2048 (23 Hz bins) 50 vs 60 Hz is not reliably separable.

## Narrowband peak tracking (`dsp/tonal.py`)

Per frame: local maxima ≥ 10 dB above a local background (1/3-octave mean of the **dB** spectrum
— a geometric mean, so one narrow peak barely raises it), 40 Hz–16 kHz, top 16 by prominence.
Each peak gets a −3 dB bandwidth and Q (flagged *resolution-limited* when the width is ≤ 2 bins:
a pure tone).

Tracks associate peaks across frames (greedy nearest match within max(1.5 bins, 1 %)), and
die after ~1 s without a match. Per track: persistence (fraction of frames present, 4 s window),
observed duration, frequency stability (σ/μ), median prominence and Q, level trend (dB/s, least
squares) and **harmonic membership** (≥ 2 simple ratio relations or ≥ 3 integer relations up to
16 with other live tracks — typical of musical notes, atypical of feedback).

## Spectral statistics

Centroid, 85 % roll-off and spectral flatness (geometric/arithmetic mean of power, 100 Hz–16 kHz)
on the long-term spectrum; flatness and HF share on the noise spectrum.

## Sibilance metric

Per active frame: 5–9 kHz energy. Metric = 95th percentile of that energy minus the **median**
200 Hz–2 kHz energy of the channel (a stable reference). Same-frame ratios were rejected because
they explode in frames that contain only a fricative.

## Performance

Measured on Windows 11, i7-13700H, 8 channels, FFT 4096, 10 Hz: ~12–18 ms average,
~20–28 ms p95 per tick (budget 100 ms) after Windows power-throttling opt-out. Without the
opt-out the same work took 60–100 ms. Health (tick avg/p95/max, overruns, CPU, callback rate,
overflows, lost frames) is shown in the status strip and `/api/health`.
