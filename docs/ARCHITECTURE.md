# Architecture

Live Audio Assist is a single local process: a Python/FastAPI backend that captures and
analyses audio, and serves a compiled Angular dashboard plus a REST/WebSocket API.

```
 ┌──────────────── Python process ───────────────────────────────────────────────────┐
 │                                                                                   │
 │  PortAudio callback thread          analysis thread (10 Hz)        asyncio loop   │
 │  ┌──────────────────────┐   ring   ┌──────────────────────────┐   ┌────────────┐  │
 │  │ SoundDeviceSource    │──buffer─▶│ MultiChannelAnalyzer     │   │ FastAPI    │  │
 │  │                      │ (4 s,    │  (FFT, levels, EMAs,     │   │  REST /api │◀─┼── browser
 │  │ copy block + counters│ bounded) │   peak tracking)         │   │  WS   /ws  │──┼─▶ dashboard
 │  └──────────────────────┘          │ DiagnosticEngine (rules, │──▶│ snapshots  │  │
 │                                    │   persistence, conflicts)│   └────────────┘  │
 │                                    │ RecommendationManager    │                   │
 │                                    │   (lifecycle, verify)    │──▶ SQLite         │
 │                                    └──────────────────────────┘                   │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

## Modules (`backend/app`)

| Package | Responsibility | Depends on |
|---|---|---|
| `config/` | `ServerConfig` (env/CLI), `AnalysisSettings` (operator settings, validated, defaults = factory reset) | – |
| `logging/` | JSON log file with rotation (`logs/app.log`, 5 × 5 MB) + readable console | – |
| `audio/devices.py` | Device discovery/inspection behind a `DeviceBackend` protocol (fake in tests); sample-rate probing; device test | sounddevice |
| `audio/ring_buffer.py` | Lock-protected, preallocated multichannel ring buffer; absolute frame cursor; reports lost frames | numpy |
| `audio/sources.py` | `SoundDeviceSource` (PortAudio stream), `CaptureStats` | ring buffer |
| `dsp/` | Pure measurement: `spectrum` (FFT, grids, peaks), `levels`, `noise` (activity, floor, hum), `tonal` (peak tracker), `analyzer` (per-channel state + snapshot) | numpy/scipy |
| `diagnostics/` | `profiles`, `rules` (16 pure functions), `engine` (persistence, confidence, hysteresis, conflicts) | dsp output only |
| `recommendations/` | `builder` (diagnostic → concrete action), `lifecycle` (states, cooldowns, verification) | diagnostics |
| `database/` | SQLAlchemy models, schema version/migrations, `Repository` | SQLAlchemy |
| `services/assist_service.py` | Orchestration: capture control, analysis loop, watchdog, snapshots | everything above |
| `api/routes.py` | REST endpoints | service |
| `websocket/hub.py` | Real-time feed with per-client pacing and focus channel | service |
| `utils/platform_tuning.py` | Windows: opt out of power throttling, 1 ms timer resolution | – |

Independence rules: DSP knows nothing about rules; rules know nothing about capture; the
recommendation builder only sees diagnostics; the frontend only displays data (no DSP).

## Threads and real-time safety

1. **Audio callback** (PortAudio thread): `ring.write(indata)` (≤ 2 slice copies into a
   preallocated array under a lock held for microseconds) + counters. No FFT, logging, I/O,
   DB or allocation-heavy work. Status flags (input overflow etc.) are counted.
2. **Analysis thread** (`analysis_rate_hz`, default 10 Hz): reads *all* frames since its
   cursor (levels/clipping see every sample) and the newest `fft_size` frames (spectrum).
   If it falls behind by more than the ring (4 s) the loss is counted, never blocking
   capture. Diagnostics for each channel run every 2nd tick, staggered across channels.
3. **Event loop**: REST and WebSocket. Each WebSocket client has its own sender task at
   `ui_refresh_hz`; a slow client cannot delay analysis.

Control operations (start/stop/settings/selection) and operator actions take the service's
control lock *before* the recommendation lock — the same order the analysis tick uses — so
no lock-order deadlock is possible.

**Watchdog** (every tick): stream finished / not running → state `error`, source released,
recovery attempted every 5 s (devices are rescanned and matched by name, since indices can
change after replugging). No callbacks for > 2 s → `stalled`.

## Data flow per tick

```
ring ──▶ measure_block (all new samples)      ─▶ RMS / peak / clip counts (all channels)
     └─▶ FFT of newest window (selected only) ─▶ power, amp²
             ├─ EMA display spectrum (0.25 s)            ─▶ UI spectrum, spectrogram column
             ├─ EMA long-term spectrum (1 s, active only) ─▶ tonal rules
             ├─ EMA noise spectrum (frames near floor)    ─▶ noise/hiss/hum rules
             ├─ EMA hum spectrum (2 s, all frames)        ─▶ hum rule
             └─ peak detection ─▶ PeakTracker             ─▶ feedback rule
ChannelAnalysis ─▶ DiagnosticEngine.evaluate ─▶ active/cleared diagnostics
                ─▶ RecommendationManager.sync_channel ─▶ recommendations ─▶ SQLite + WS
```

## Persistence

SQLite at `data/live_audio_assist.db` (WAL mode). Tables: `settings` (JSON values),
`device_configs`, `channels`, `diagnostic_history`, `recommendation_history`,
`operator_actions`, `schema_version`. Schema is created by SQLAlchemy; `MIGRATIONS` in
`database/db.py` holds ordered SQL steps for future versions. Diagnostics are written when
raised/cleared and at most every 5 s while active. Raw audio is never stored.

## Frontend (`frontend/`)

Angular 22, standalone components, signals, zoneless change detection.

- `services/live.service.ts` – WebSocket client, reconnect with back-off, state as signals.
- `services/api.service.ts` – typed REST wrapper.
- `components/` – top bar, status strip, channel panel, analysis panel, recommendation
  panel, dialogs (details, history, channel editor, settings).
- `charts/` – ECharts spectrum and band chart (tree-shaken build), canvas spectrogram.

In production FastAPI serves `frontend/dist/live-audio-assist/browser` with an SPA
fallback. In development `ng serve` proxies `/api` and `/ws` to the backend
(`frontend/proxy.conf.json`).
