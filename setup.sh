#!/usr/bin/env bash
# One-time setup for Live Audio Assist on macOS (also works on Linux).
#   ./setup.sh                 full setup
#   ./setup.sh --skip-frontend backend only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"
VENV_PY="$VENV/bin/python"
SKIP_FRONTEND=0
[[ "${1:-}" == "--skip-frontend" ]] && SKIP_FRONTEND=1

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m[ok]\033[0m %s\n' "$1"; }
warn() { printf '    \033[33m[warn]\033[0m %s\n' "$1"; }
fail() { printf '    \033[31m[error]\033[0m %s\n' "$1"; exit 1; }

echo "Live Audio Assist - setup ($(uname -s))"

# ------------------------------------------------------------------ 1. Python
step "Detecting Python 3.12+"
PY=""
for c in python3.13 python3.12 python3.14 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    v="$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    major="${v%%.*}"; minor="${v##*.}"
    if [[ "$major" == "3" && -n "$minor" && "$minor" -ge 12 ]]; then PY="$c"; PYV="$v"; break; fi
  fi
done
[[ -z "$PY" ]] && fail "Python 3.12+ not found. Install it (e.g. 'brew install python@3.13' or python.org) and re-run."
ok "Python $PYV ($PY)"
[[ "${PYV##*.}" -ge 14 ]] && warn "Python 3.14+: if NumPy/SciPy wheels are missing, install Python 3.13 and re-run."

# ------------------------------------------------------------------ 2. venv
step "Creating virtual environment (backend/.venv)"
if [[ -x "$VENV_PY" ]]; then ok "already exists"; else "$PY" -m venv "$VENV" || fail "venv creation failed"; ok "created"; fi

# ------------------------------------------------------------------ 3. Python deps
step "Installing Python dependencies"
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r "$BACKEND/requirements-dev.txt" --quiet || fail "pip install failed"
ok "installed"

# ------------------------------------------------------------------ 4/5. Frontend
if [[ "$SKIP_FRONTEND" == "1" ]]; then
  warn "Skipping frontend build (--skip-frontend). The backend will show a placeholder page."
else
  step "Checking Node.js / npm"
  command -v npm >/dev/null 2>&1 || fail "Node.js (npm) not found. Install Node.js LTS ('brew install node' or nodejs.org) and re-run, or use --skip-frontend."
  ok "Node $(node --version)"
  step "Installing frontend dependencies (npm ci)"
  ( cd "$FRONTEND" && if [[ -f package-lock.json ]]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi ) || fail "npm install failed"
  ok "installed"
  step "Building the dashboard (Angular production build)"
  ( cd "$FRONTEND" && npm run build ) || fail "Angular build failed"
  ok "built -> frontend/dist/live-audio-assist/browser"
fi

# ------------------------------------------------------------------ 6. Validation
step "Validating audio subsystem and configuration"
( cd "$BACKEND" && "$VENV_PY" - <<'PYEOF'
from app.config.settings import ServerConfig, AnalysisSettings
from app.database.db import Database
cfg = ServerConfig.from_env()
cfg.data_dir.mkdir(parents=True, exist_ok=True)
cfg.logs_dir.mkdir(parents=True, exist_ok=True)
Database(cfg.db_path)
AnalysisSettings()
print("    [ok] configuration and database (" + str(cfg.db_path) + ")")
try:
    import sounddevice as sd
    devs = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
    print("    [ok] PortAudio " + sd.get_portaudio_version()[1] + " - " + str(len(devs)) + " input device(s) visible")
except Exception as exc:
    print("    [warn] PortAudio not usable (" + str(exc) + "). Audio capture will not be available.")
PYEOF
) || fail "validation failed"

if [[ "$(uname -s)" == "Darwin" ]]; then
  warn "macOS: the first capture will trigger a microphone-permission prompt for your terminal app."
  warn "If no audio arrives, enable it under System Settings > Privacy & Security > Microphone."
fi

printf '\n\033[32mSetup complete.\033[0m Start the application with:\n'
echo "    ./start.sh"
