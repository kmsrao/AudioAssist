# Troubleshooting

Logs: terminal output and `logs/app.log` (JSON lines, rotated at 5 MB × 5). Health details:
status strip in the dashboard or `GET /api/health`.

## Setup / start

| Symptom | Fix |
|---|---|
| `setup.ps1 cannot be loaded because running scripts is disabled` | `powershell -ExecutionPolicy Bypass -File .\setup.ps1` (same for `start.ps1`) |
| "Python 3.12 or newer not found" | install Python 3.13 (`winget install Python.Python.3.13` / `brew install python@3.13`), open a new terminal |
| pip fails building NumPy/SciPy | your Python is too new for available wheels — use 3.13 or 3.12 and delete `backend/.venv` before re-running setup |
| "Node.js (npm) not found" | install Node.js LTS and open a new terminal, or `setup -SkipFrontend` / `--skip-frontend` |
| Browser shows "backend is running … dashboard not found" | the Angular build is missing: re-run setup (or `cd frontend && npm run build`) |
| Port already in use | `start.ps1 -Port 9000` / `./start.sh --port 9000`, or stop the other instance |
| `./start.sh: Permission denied` | `chmod +x setup.sh start.sh` |

## Audio

| Symptom | Likely cause / fix |
|---|---|
| Device missing from the list | connect it, stop capture, press **Rescan devices**. On Windows also check the device isn't disabled in Sound settings |
| "Configuration rejected by driver" / capture start fails | the driver does not accept that sample rate / channel count / buffer. Try 48 kHz, the device's listed rates, fewer channels, block 512/1024. On Windows try the WASAPI or ASIO entry of the same device |
| Capture "running" but meters stay at the floor | OS microphone permission (Windows: Privacy & security → Microphone; macOS: Privacy & Security → Microphone for your terminal app); wrong input selected; interface routing/direct outs not configured |
| Only 2 channels available | you picked the MME/DirectSound entry. Use ASIO or WASAPI (Windows) or an aggregate device (macOS) |
| "Device is busy" / exclusive mode errors | another program (DAW, recording software) holds the device; close it or use a multi-client driver |
| Capture state **error**: "Audio stream stopped unexpectedly (device disconnected?)" | cable/USB/power interruption. The app retries every 5 s and reconnects when the device (matched by name) returns; or restart capture from **Audio & Settings** |
| Capture **stalled** | no callbacks for > 2 s: driver hang, sleep/hibernate, USB power saving. Restart capture; disable USB selective suspend on Windows |
| Input overflows increasing | buffer too small for the machine/driver. Increase block size (1024) |

## Analysis / performance

| Symptom | Fix |
|---|---|
| Processing health red, "overruns" rising | lower **Analysis rate** (e.g. 5 Hz), reduce **FFT size** to 2048, analyse fewer channels, or close CPU-heavy apps. On Windows laptops use the *Best performance* power mode and keep the charger connected |
| UI sluggish on an old machine | lower **UI refresh** to 5 Hz (or per tab via WebSocket `rate`) |
| Recommendations seem slow to appear | by design: tonal issues need `diagnostic_persistence_s` (3 s) of evidence + 2.5 s settle; lower persistence for faster but noisier advice |
| Too many / too few recommendations | raise/lower **Minimum confidence**; choose the correct **profile** for each channel (a keyboard on a speech profile looks "muddy"); dismiss items you consider intentional (cooldown 120 s) |
| Feedback warning on a sustained note | dismiss it; prefer the correct instrument profile (instrument profiles reduce or disable feedback sensitivity) |
| Hum reported at the wrong mains frequency | set **Mains frequency** to 50 or 60 Hz; use FFT ≥ 4096 |
| SNR shows "—" | no distinguishable quiet periods yet (continuous material). This is intentional: the app does not invent an SNR |

## Reset

- **Audio & Settings → Analysis & limits → Reset to defaults** restores analysis settings (keeps
  the audio input).
- To start completely fresh, stop the app and delete `data/live_audio_assist.db`.
