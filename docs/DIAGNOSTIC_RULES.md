# Diagnostic rules and recommendation logic

The engine is deterministic and explainable: every diagnostic carries its evidence, an
explanation sentence, a severity and a confidence with its breakdown. Wording is deliberately
hedged ("potential", "possible", "likely"). FFT analysis cannot diagnose everything; these are
prompts for an engineer's ears, not verdicts.

## Tonal balance relative to the profile

`deviation(f) = long-term spectrum(f) − profile reference(f) − offset`, on the 1/3-octave grid,
where `offset` is the **median** difference over the profile's analysis range (default
100 Hz–8 kHz). Removing the median makes the comparison level-independent and robust against a
single large bump. References are generic smooth curves (speech loosely follows published
long-term average speech spectra); they describe context, not a target to EQ towards.

Tonal rules evaluate only when the channel is **active** and ≥ 2 s of active signal has been
accumulated. Threshold = base + profile tolerance.

| Rule | Range | Trigger | Recommendation |
|---|---|---|---|
| muddy | 200–500 Hz | peak excess ≥ 7 dB (+tol) and mean ≥ half of that | bell cut |
| boxy | 400–800 Hz | same, 7 dB | bell cut |
| boomy | 100–250 Hz | same, 7 dB | bell cut (+ mic distance hint) |
| low_mid_excess | 150–600 Hz | same, 5 dB (broad rule for keyboard/choir/general/bass) | broad bell cut |
| harsh | 2.5–5 kHz | same, 7 dB | bell cut (dynamic EQ hint) |
| weak_presence | 1–3 kHz | mean deficit ≥ 6 dB (+tol) | small boost ≤ max boost, Q 1 |
| sibilance | 5–9 kHz | p95 burst energy − median body ≥ 10 dB (+tol) | de-esser / dynamic EQ (static fallback) |
| spectral_concentration | any 1/3 oct, 40 Hz–12 kHz | ≥ 55 % of energy in one 1/3 octave | investigate, ≤ 3 dB cut |

Target frequency = location of the largest deviation inside the range (dynamic, never a fixed
value). Q = centre / (−3 dB width of the bump on the deviation curve), clamped per issue
(muddy 0.7–1.5, boxy 1.0–2.0, boomy 0.7–1.4, low-mid 0.5–1.0, harsh 1.5–3.0).

## Low frequency, noise and hum

| Rule | Evidence | Trigger | Recommendation |
|---|---|---|---|
| rumble | share of energy below the profile's rumble limit (70 Hz for vocals; 25 Hz bass/kick; 120 Hz cymbals) in active **or** noise spectrum | ≥ 15 % and 20–60 Hz band > −70 dBFS | HPF in the profile range, clamped to HPF min/max settings; higher end when severe |
| hum | mains harmonics (see DSP_DESIGN) | ≥ 3 harmonics, or fundamental ≥ 15 dB + 2nd ≥ 10 dB; fundamental > −95 dBFS | fix at source (cables, ground lift, power); fallback notches at f and 2f, Q 10, −6 dB |
| broadband_noise | noise-floor spectrum from pauses | level ≥ −60 dBFS, flatness ≥ 0.2, HF share ≤ 60 % | gain structure / source checks; gate as operator option |
| hiss | noise-floor spectrum | level ≥ −66 dBFS and > 60 % of noise energy above 4 kHz | gain / source checks; optional −2 dB shelf above 8 kHz |

For bass, kick and keyboard, hum is only judged from pauses (musical notes sit on 55/110 Hz);
kick does not run the hum rule at all.

## Dynamics

| Rule | Trigger | Recommendation |
|---|---|---|
| clipping | ≥ 2 analysis periods in the last 5 s with ≥ 3 consecutive samples at full scale | trim −3 dB (−6 dB if ≥ 5 events) or pad |
| low_headroom | peaks > −1 dBFS in ≥ 20 % of active periods, no clipping | trim −3 dB |
| weak_signal | typical active level (p90 RMS) ≥ 18 dB below the profile's nominal level (e.g. −24 dBFS speech), peak hold < −20 dBFS, channel above the activity gate | gain + min(shortfall − 6, headroom to −6 dBFS peaks, max gain change), ≥ 3 dB |

## Feedback risk

A single peak is **never** enough. A tracked peak qualifies only if **all** hold:

- 100 Hz – 12 kHz;
- observed ≥ `2 s / sensitivity` (clamped 1.5–5 s) with ≥ 85 % persistence;
- narrow: resolution-limited or Q ≥ 20;
- frequency stability σ/μ ≤ 0.3 %;
- prominence ≥ 15 dB above the local background.

Score = `sensitivity × (0.4 + 0.3·prominence_term + 0.2·persistence_term + 0.1·growth_term)`,
halved if the peak belongs to a harmonic series (a sustained note). Triggers at ≥ 0.4.
Profile sensitivity: speech 1.2, choir 1.3, vocals 1.0, acoustic guitar 0.8, electric guitar 0.7,
general 0.6; keyboard, bass, kick, snare and cymbals 0 (not assessed: DI sources and sustained
notes produce legitimate stable peaks). Severity is *critical* when the level rises > 1.5 dB/s.

Recommendation: check mic position / reduce the monitor send first, then a narrow cut (−3 dB, or
−6 dB when critical; Q from the measured width, 8–16; 10 when resolution-limited) on the path
that rings.

## Persistence, clearing and confidence

Each (channel, rule) keeps a window of recent results: *triggered*, *not triggered* or
*not evaluable* (silence, insufficient data — skipped). Window length =
`diagnostic_persistence_s` (default 3 s) × rule factor (clipping 0.2, feedback 0.5, sibilance /
presence / noise 1.5, weak signal 2.0).

- **Raise** when ≥ 60 % of the window is evaluable and ≥ 70 % of evaluable ticks triggered.
- **Clear** when < 25 % of the last `clear_time_s` (4 s) evaluable ticks triggered (hysteresis).

**Confidence** (all terms 0–1, shown in the details dialog):

```
evidence    = 0.4 + 0.6 × mean strength of triggered ticks   (strength = how far past threshold)
persistence = triggered / evaluable ticks in the window
context     = (1.0 if the rule is a priority for the profile else 0.75) × min(1, evaluable / window)
confidence  = 0.5 × evidence + 0.3 × persistence + 0.2 × context
```

Labels: high ≥ 0.75, moderate ≥ 0.5, low < 0.5. Recommendations require ≥ `min_confidence`
(0.55).

## Conflicts and suppression

Suppressed diagnostics stay visible ("after …") but produce no recommendation:

- **clipping** suppresses weak_signal and low_headroom;
- **hum** suppresses rumble, and boomy/muddy/boxy/low-mid/concentration within 1/6 octave of a
  hum harmonic (fix the hum first);
- **feedback_risk** suppresses harsh, weak_presence, boxy, muddy, concentration within 1/3 octave;
- overlapping **low-mid** diagnostics (targets within 0.75 octave) describe one bump: the one
  whose range is centred nearest the bump wins; a diagnosis below `min_confidence` loses to an
  actionable one; the incumbent gets a 0.2-octave bonus (stability);
- **weak_presence** waits for harshness and low-mid excess to be addressed ("cut before boost");
- **spectral_concentration** yields to a more specific tonal/LF diagnosis at the same place.

## Recommendation lifecycle

```
new ──(new_to_pending_s, 10 s)──▶ pending ──operator──▶ applied ──condition clears──▶ resolved
  │                                  │                     │
  │                                  └──operator──▶ dismissed (cooldown 120 s)
  └── condition clears / superseded / channel deselected / device changed ──▶ expired (cooldown 20 s)
```

- One live recommendation per (channel, issue); updates refresh it in place until the operator acts.
- Tonal/noise recommendations appear only after the diagnosis has been **continuously
  unsuppressed for 2.5 s** of analysed audio, so a root cause (e.g. hum) can be confirmed first.
  Clipping and feedback are not delayed.
- **Mark applied** stores a *before* snapshot (rule metric, band levels, level), resets the
  channel's evidence and schedules verification after `verification_window_s` (8 s):
  *resolved* if the condition cleared, otherwise *improved* (metric ≥ 2 units **and** ≥ 25 % better) or
  *persists*. Applying again after "persists" starts a new verification.
- **Re-analyse** restarts evidence collection and records a fresh *after* snapshot.

## Limits (settings)

`max_eq_cut_db` (6), `max_eq_boost_db` (3), `hpf_min_hz`/`hpf_max_hz` (20/250),
`max_gain_change_db` (12), `min_confidence` (0.55), `diagnostic_persistence_s` (3),
`clear_time_s` (4), `recommendation_cooldown_s` (120), `verification_window_s` (8).
Cut depth = half the measured excess in 0.5 dB steps, between 2 dB and the max cut.

## Calibration status

Thresholds were tuned with synthetic signals (`scripts/calibrate.py`, `demo` and `clean`
scenarios) so that each planted problem is detected and clean material is not flagged. They
have **not** been tuned on a corpus of real live recordings yet — expect to adjust tolerances per
venue/source, and treat recommendations as starting points.
