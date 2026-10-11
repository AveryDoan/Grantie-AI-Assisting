#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
if [[ -x "$ROOT/.venv/bin/uvicorn" ]]; then
  UVICORN="$ROOT/.venv/bin/uvicorn"
elif [[ -x "$ROOT/../Avery/.venv/bin/uvicorn" ]]; then
  UVICORN="$ROOT/../Avery/.venv/bin/uvicorn"
else
  echo "No project virtualenv found. Create Combine/.venv and install the project dependencies first." >&2
  exit 1
fi

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$ROOT"
APP_MODE=demo "$UVICORN" app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

cd "$ROOT/frontend"
npm run dev -- --host 127.0.0.1 &
FRONTEND_PID=$!

echo "Grantie frontend: http://127.0.0.1:5173/"
echo "Avery API docs:   http://127.0.0.1:8000/docs"
wait "$BACKEND_PID" "$FRONTEND_PID"
