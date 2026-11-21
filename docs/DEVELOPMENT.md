# Development

## Layout

```
backend/app/        FastAPI application (see ARCHITECTURE.md)
backend/tests/      pytest suite (no hardware required)
frontend/src/app/   Angular 22 dashboard (standalone components, signals, zoneless)
scripts/            calibrate.py - offline diagnostic calibration harness
data/  logs/        runtime SQLite database and rotated logs (git-ignored)
docs/               documentation
```

## Running in development

Backend with auto-reload:

```bash
cd backend
.venv/bin/python -m uvicorn app.main:create_app --factory --reload --port 8765     # macOS
.venv\Scripts\python -m uvicorn app.main:create_app --factory --reload --port 8765 # Windows
```

Frontend with hot reload (proxies `/api` and `/ws` to :8765, see `frontend/proxy.conf.json`):

```bash
cd frontend
npm start            # http://localhost:4200
```

Or both at once: `./start.sh --dev` / `.\start.ps1 -Dev`.

Environment variables: `LAA_HOST`, `LAA_PORT`, `LAA_NO_BROWSER=1`,
`LAA_DATA_DIR`, `LAA_LOGS_DIR`, `LAA_LOG_LEVEL`. Use a separate `LAA_DATA_DIR` for experiments so
you don't overwrite your show configuration.

## Tests

Backend (105 tests, ~20–80 s depending on the machine):

```bash
cd backend
.venv/bin/python -m pytest          # or .venv\Scripts\python -m pytest
```

| File | Covers |
|---|---|
| `test_dsp.py` | FFT/PSD scaling, Parseval, bands, grids, stats, peak detection/Q, RMS/peak/crest, clipping runs, analyser smoothing & spectrogram, ring buffer (wrap, lost frames, padding) |
| `test_synth_and_noise.py` | streaming generators, determinism, white/pink tilt, clipping/rumble generators, scenarios, synthetic source thread + loop cache, hum detection (positive and negative), level history/SNR, peak tracker persistence and harmonics |
| `test_diagnostics.py` | every major rule on synthetic signals (muddy, harsh, sibilance, clipping, rumble+HPF, hum+suppression, feedback, weak signal, broadband noise), negatives (clean speech, transient tone burst, keyboard partials, silence), persistence, clearing, confidence formula, required fields, profile consistency |
| `test_recommendations.py` | cut/boost limits, dynamic targets, HPF/gain/notch/hum actions, lifecycle (settle, pending, apply→resolved/persists/improved, dismiss cooldown, expiry, suppression, deselection, ranking) |
| `test_persistence_devices.py` | settings/channels/history/actions persistence, schema version, corrupt settings fallback, validation, device discovery with a fake PortAudio backend |
| `test_service_capture.py` | watchdog disconnect + recovery, stall detection, default-input fallback, hardware device switch, unsupported rate, missing device, unselected channels, ring overrun accounting, channel reset, applied-but-unfixed vs applied-and-fixed verification |
| `test_api.py` | REST endpoints end-to-end (synthetic audio), selection by group, apply/dismiss/re-analyse flow, settings validation/reset, capture control, SPA fallback, WebSocket stream |

Frontend (15 tests, Vitest via the Angular CLI):

```bash
cd frontend
npx ng test --watch=false
```

Covers formatting helpers, `LiveService` message handling (focus, spectrogram history),
channel panel (levels, badges, clip LED, search, selection API calls) and recommendation panel
(content, operator actions, focus filter).

## Calibration harness

```bash
cd backend
.venv/bin/python ../scripts/calibrate.py demo 40
.venv/bin/python ../scripts/calibrate.py clean 40 speech,keyboard,general
```

Runs a synthetic test scenario (`backend/tests/synth/`) through the analyser and engine faster than real time and prints, per
channel, the deviation from the profile reference at key frequencies and the diagnostics raised
(with time to first detection). Use it when changing thresholds, profiles or DSP: every planted
issue in `demo` should be found, and `clean` should stay clean.

## Adding a diagnostic rule

1. Write a pure function in `diagnostics/rules.py` returning `None` / `RuleResult(...)` with
   evidence and an explanation sentence.
2. Register a `RuleSpec` in `RULES` (title, group, persistence factor).
3. Enable it in the relevant profiles (`diagnostics/profiles.py`) and add a priority if needed.
4. Add an action in `recommendations/builder.py` and conflicts in `engine._resolve_conflicts`.
5. Add positive and negative tests in `tests/test_diagnostics.py`; run the calibration harness.
6. Document it in `DIAGNOSTIC_RULES.md` and the frontend `CATEGORY_LABEL`.

## Code conventions

- DSP functions are pure and vectorised; no I/O or logging in the audio callback.
- Rules never touch DSP state; recommendations never touch audio.
- Timestamps shown to users are wall clock (`time.time()`); durations use analysed time or
  `time.monotonic()`.
