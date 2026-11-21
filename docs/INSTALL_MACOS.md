# Installation — macOS

> **Status:** the macOS path is implemented but **has not been run on a Mac yet** (development
> and validation were done on Windows 11). Please report anything that differs.

## Prerequisites

| Tool | Install |
|---|---|
| Python 3.12+ (3.13 recommended) | `brew install python@3.13` or <https://www.python.org/downloads/macos/> |
| Node.js LTS (to build the dashboard) | `brew install node` or <https://nodejs.org> |

PortAudio is bundled in the `sounddevice` wheel for macOS (Intel and Apple Silicon); no
Homebrew `portaudio` is needed.

## Setup

```bash
cd path/to/AudioAssist
chmod +x setup.sh start.sh     # only if the executable bit was lost (e.g. zip download)
./setup.sh
```

`./setup.sh --skip-frontend` sets up only the backend.

## Start

```bash
./start.sh            # opens http://localhost:8765
./start.sh --port 9000 --no-browser
./start.sh --dev      # backend + Angular dev server on :4200
```

Stop with **Ctrl+C**. Logs: terminal and `logs/app.log`.

## macOS-specific notes

- **Microphone permission.** macOS asks the *terminal application* (Terminal, iTerm, VS Code)
  for microphone access the first time a stream is opened. If you denied it, enable it in
  System Settings → Privacy & Security → Microphone, then restart the terminal. Without
  permission, streams open but deliver silence (the channel meters stay at the floor).
- **Core Audio** is the only host API. Aggregate devices created in *Audio MIDI Setup* appear
  as one device with the combined inputs — useful to combine interfaces, but drift
  correction must be enabled for the non-clock-master device.
- **Sample rate.** Core Audio devices have one active hardware rate (set in Audio MIDI Setup).
  Opening a stream at a different rate may be resampled or refused depending on the driver;
  match the rate there.
- **Gatekeeper / quarantine.** If scripts were downloaded as a zip, run
  `xattr -dr com.apple.quarantine .` in the project folder if macOS blocks them.
