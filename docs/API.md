# API reference

Base URL: `http://localhost:8765`. Interactive OpenAPI docs: **`/docs`** (Swagger UI) and
`/openapi.json`. All REST endpoints are under `/api`; JSON in/out. Channel numbers in the API
are **1-based** (CH01 = 1). No endpoint modifies audio or controls a mixer.

## Status

| Method | Path | Description |
|---|---|---|
| GET | `/api/status` | app/version, device, capture status, sample rate, channels total/analysed/needing attention, active recommendations, health |
| GET | `/api/health` | capture (state, callbacks/s, overflows, last callback age, lost frames), processing (tick avg/p95/max, budget, overruns), CPU % |

## Devices and capture

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/devices?refresh=false` | | input devices (index, name, host API, channels, rates, latency, default flag), recently used devices, options. `refresh=true` rescans PortAudio (409 while a hardware stream is open) |
| GET | `/api/devices/{index}` | | one device |
| POST | `/api/devices/{index}/test` | `{channels?, sample_rate?, duration_s?}` | 1 s test capture → per-channel RMS/peak; reuses the live stream if capture is on that device |
| GET | `/api/capture` | | capture status |
| POST | `/api/capture/start` · `/stop` · `/restart` | | control capture (409 with message on driver errors) |
| POST | `/api/capture/device` | `{device_index, sample_rate?, block_size?, channel_count?, start: true}` | change the input; validates rate/channel count against the device |

## Channels

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/channels` | | `{channels: [{index, number, name, profile, group, selected, enabled}], groups}` |
| PUT | `/api/channels` | `{channels: [...]}` | upsert channel configs (422 unknown profile, 400 unknown group) |
| GET | `/api/channels/selected` | | `{selected: [1, 2, …]}` |
| PUT | `/api/channels/selected` | one of `{selected: [..]}`, `{select_all: true}`, `{deselect_all: true}`, `{group: "Drums"}`, `{add_group: "Vocals"}` | change selection |
| GET | `/api/channels/{n}/analysis?spectrogram=false` | | latest analysis of a selected channel (levels, spectrum on the grid, bands, stats, persistent peaks, optional spectrogram) |
| POST | `/api/channels/{n}/reanalyze` | | reset that channel's evidence |
| GET/PUT | `/api/groups` | `{groups: [...]}` | channel groups |
| GET | `/api/profiles` | | the 12 profiles with rules, priorities, tolerances, HPF ranges, reference curves |
| GET | `/api/analysis/grid` | | frequency grid for spectrum/spectrogram arrays, FFT size, bin width |

## Diagnostics and recommendations

| Method | Path | Description |
|---|---|---|
| GET | `/api/diagnostics?channel=` | active diagnostics incl. suppressed ones (`suppressed_by`) |
| GET | `/api/diagnostics/history?channel=&limit=` | stored diagnostics |
| GET | `/api/recommendations` | `{version, active: [...], recent: [...]}` ranked by severity then confidence |
| GET | `/api/recommendations/history?channel=&status=&limit=` | stored recommendations |
| GET | `/api/recommendations/{id}` | one recommendation (live or stored) |
| POST | `/api/recommendations/{id}/applied` | `{note?}` — operator made the adjustment → verification (409 if dismissed/closed) |
| POST | `/api/recommendations/{id}/dismissed` | `{note?}` — dismiss, starts cooldown |
| POST | `/api/recommendations/{id}/reanalyze` | restart evidence; before/after comparison |
| POST | `/api/reanalyze?channel=` | reset evidence for one or all selected channels |
| GET | `/api/operator-actions?limit=` | audit log of operator actions |

### Recommendation object (abridged)

```json
{
  "id": "9543afaecad2", "channel": 2, "channel_name": "Worship Leader", "profile": "lead_vocal",
  "category": "harsh", "issue": "Potential harshness", "severity": "high",
  "confidence": 0.94, "confidence_label": "high",
  "control": "Channel EQ - parametric band", "filter_type": "bell",
  "target_hz": 3710.0, "gain_db": -6.0, "q": 3.0,
  "hpf_hz": null, "hpf_slope": null, "gain_change_db": null,
  "summary": "Channel EQ - parametric band: -6 dB at 3.7 kHz, Q 3",
  "actions": ["Set a bell band at ~3.7 kHz, Q 3.0, and cut 6 dB.", "..."],
  "explanation": "Long-term energy around 3.7 kHz is 12.0 dB above the Lead Vocal reference shape ...",
  "expected_improvement": "Smoother upper mids, less listening fatigue.",
  "caveats": ["..."], "fallback": null,
  "supporting_analysis": {"evidence": {...}, "confidence": {"evidence": 0.9, "persistence": 1.0, "context": 1.0, "formula": "..."}, "measured": {"value": 12.0, "unit": "dB"}},
  "status": "pending", "created_at": 1795195392.07, "updated_at": ..., "timestamp": ...,
  "history": [{"from": null, "to": "new", "at": ..., "note": null}, ...],
  "before": null, "after": null, "verification": null
}
```

## Settings

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/settings` | | current settings, defaults, allowed options |
| POST / PUT | `/api/settings` | `{values: {fft_size: 8192, min_confidence: 0.6, ...}}` | partial update; validated (422 on invalid/unknown keys). Capture-related keys restart capture; DSP keys rebuild the analyser |
| POST | `/api/settings/reset` | | factory defaults, keeping the current audio input |

Setting keys: see `AnalysisSettings` in `backend/app/config/settings.py` (audio source, DSP,
diagnostics, recommendation limits, UI rate).

## WebSocket `/ws`

Server → client messages (JSON, `type` field):

| type | when | content |
|---|---|---|
| `hello` | on connect and whenever channels/groups/settings/grid change | grid, channels, groups, profiles, settings |
| `status` | ~1 Hz | same as `/api/status` |
| `levels` | every UI tick | per channel: rms, peak, peak_hold, clip, active, selected, diagnostics count |
| `analysis` | every UI tick with new data, for the focused channel | as `/api/channels/{n}/analysis`; `full: true` (with full spectrogram) after a focus change, otherwise `spectrogram_column` |
| `recommendations` | when the version changes | `{version, active, recent}` |
| `diagnostics` | when changed | `{active: [...]}` |

Client → server: `{"type":"focus","channel":3}`, `{"type":"rate","hz":5}` (1–30), `{"type":"ping"}`.

Raw audio is never sent to the browser.
