# Installation — Windows 11

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.12 or 3.13 recommended (3.14+ works if NumPy/SciPy wheels exist) | <https://www.python.org/downloads/> or `winget install Python.Python.3.13` |
| Node.js | LTS (only to build the dashboard) | <https://nodejs.org> or `winget install OpenJS.NodeJS.LTS` |

PortAudio does **not** need a separate install: the `sounddevice` wheel bundles it.

## Setup

```powershell
cd path\to\AudioAssist
.\setup.ps1
```

If PowerShell refuses to run scripts:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

`setup.ps1` detects Python (tries `py -3.13`, `py -3.12`, `py -3.14`, `python`, `python3`),
creates `backend\.venv`, installs Python packages, runs `npm ci` and the production Angular
build, then validates the configuration and lists how many input devices PortAudio sees.
`.\setup.ps1 -SkipFrontend` sets up only the backend.

## Start

```powershell
.\start.ps1                 # configured source; opens http://localhost:8765
.\start.ps1 -Port 9000 -NoBrowser
.\start.ps1 -Dev            # backend + Angular dev server with hot reload (http://localhost:4200)
```

Logs appear in the terminal and in `logs\app.log`. Stop with **Ctrl+C**.

## Windows-specific notes

- **Host APIs.** The same physical input appears several times (MME, DirectSound, WASAPI,
  WDM-KS, and ASIO if a driver is installed). Prefer the interface's **ASIO** entry for
  multichannel interfaces, otherwise **WASAPI**. MME/DirectSound often expose only 2 channels
  and add latency. See [AUDIO_SETUP](AUDIO_SETUP.md).
- **WASAPI shared mode** runs at the device's Windows mix format; Live Audio Assist enables
  PortAudio's `auto_convert`, so other rates are resampled by Windows. Probing reports only
  the native mix rate.
- **Exclusive drivers.** Many ASIO/WDM-KS drivers allow one client. Close the DAW or other
  software that holds the device, or use the interface's multi-client driver. The device
  test reuses the running stream if capture is already using that device.
- **Microphone privacy.** Settings → Privacy & security → Microphone → allow desktop apps.
  Without it, streams open but deliver silence.
- **Power throttling.** On hybrid Intel CPUs Windows may move bursty processes to efficiency
  cores ("EcoQoS"). The app opts itself out of execution-speed throttling at start
  (measured 3–4× faster analysis). Use a "Best performance" power mode during shows.
