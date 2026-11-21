#!/usr/bin/env bash
# Start Live Audio Assist (macOS / Linux). Stop with Ctrl+C.
#   ./start.sh                     configured audio interface, opens the browser
#   ./start.sh --port 9000 --no-browser
#   ./start.sh --dev               backend + Angular dev server (hot reload) on :4200
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV_PY="$BACKEND/.venv/bin/python"

PORT=8765
HOST=127.0.0.1
DEV=0
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-browser) ARGS+=(--no-browser) ;;
    --port) PORT="$2"; shift ;;
    --host) HOST="$2"; shift ;;
    --log-level) ARGS+=(--log-level "$2"); shift ;;
    --dev) DEV=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
  shift
done

if [[ ! -x "$VENV_PY" ]]; then
  echo -e "\033[31m[error]\033[0m Python environment not found. Run ./setup.sh first."; exit 1
fi
if [[ "$DEV" == "0" && ! -f "$FRONTEND/dist/live-audio-assist/browser/index.html" ]]; then
  echo -e "\033[33m[warn]\033[0m Compiled dashboard not found - run ./setup.sh (the API still works; a placeholder page is shown)."
fi

cd "$BACKEND"
if [[ "$DEV" == "1" ]]; then
  command -v npm >/dev/null 2>&1 || { echo "[error] npm not found (needed for --dev)"; exit 1; }
  "$VENV_PY" -m app.main --host "$HOST" --port "$PORT" --no-browser "${ARGS[@]+"${ARGS[@]}"}" &
  BACKEND_PID=$!
  trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT INT TERM
  ( sleep 8; command -v open >/dev/null && open "http://localhost:4200" ) &
  cd "$FRONTEND" && npm start
else
  exec "$VENV_PY" -m app.main --host "$HOST" --port "$PORT" "${ARGS[@]+"${ARGS[@]}"}"
fi
