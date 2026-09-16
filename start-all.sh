#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE_DIR="$PROJECT_ROOT/backend/.runtime/service-manager-linux"
STATE_FILE="$STATE_DIR/services.env"
PYTHON="$PROJECT_ROOT/backend/.runtime/linux/python/bin/python"
NODE="$PROJECT_ROOT/backend/.runtime/linux/node/bin/node"
VITE="$PROJECT_ROOT/frontend/node_modules/vite/bin/vite.js"
BACKEND_HOST=${BACKEND_HOST:-127.0.0.1}
FRONTEND_HOST=${FRONTEND_HOST:-127.0.0.1}
BACKEND_START_PORT=${BACKEND_PORT:-8000}
FRONTEND_START_PORT=${FRONTEND_PORT:-5173}

[ -x "$PYTHON" ] || { echo "Python runtime is missing; run sh backend/scripts/setup_linux.sh" >&2; exit 1; }
[ -x "$NODE" ] || NODE=$(command -v node || true)
[ -n "$NODE" ] && [ -f "$VITE" ] || { echo "Frontend runtime is missing; run sh backend/scripts/setup_linux.sh" >&2; exit 1; }
mkdir -p "$STATE_DIR"

if [ -f "$STATE_FILE" ]; then
  # shellcheck disable=SC1090
  . "$STATE_FILE"
  if kill -0 "${BACKEND_PID:-0}" 2>/dev/null && kill -0 "${FRONTEND_PID:-0}" 2>/dev/null; then
    echo "Agent Eval services are already running: $FRONTEND_URL"
    exit 0
  fi
  sh "$PROJECT_ROOT/stop-all.sh" >/dev/null 2>&1 || true
fi

find_port() {
  "$PYTHON" - "$1" "$2" <<'PY'
import socket, sys
host, start = sys.argv[1], int(sys.argv[2])
for port in range(start, min(start + 200, 65536)):
    with socket.socket() as sock:
        try:
            sock.bind((host, port))
        except OSError:
            continue
        print(port)
        break
else:
    raise SystemExit("no free port found")
PY
}

BACKEND_PORT=$(find_port "$BACKEND_HOST" "$BACKEND_START_PORT")
FRONTEND_PORT=$(find_port "$FRONTEND_HOST" "$FRONTEND_START_PORT")
[ "$FRONTEND_PORT" != "$BACKEND_PORT" ] || FRONTEND_PORT=$(find_port "$FRONTEND_HOST" "$((FRONTEND_PORT + 1))")
BACKEND_URL="http://127.0.0.1:$BACKEND_PORT"
FRONTEND_URL="http://127.0.0.1:$FRONTEND_PORT"

cd "$PROJECT_ROOT"
nohup "$PYTHON" backend/run_server.py --host "$BACKEND_HOST" --port "$BACKEND_PORT" >"$STATE_DIR/backend.stdout.log" 2>"$STATE_DIR/backend.stderr.log" &
BACKEND_PID=$!
cleanup() { kill "$BACKEND_PID" "${FRONTEND_PID:-0}" 2>/dev/null || true; }
trap cleanup INT TERM EXIT

ready=0
for _ in $(seq 1 150); do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    cat "$STATE_DIR/backend.stderr.log" >&2
    exit 1
  fi
  if "$PYTHON" - "$BACKEND_URL/api/health" <<'PY' >/dev/null 2>&1
import sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=1) as response:
    raise SystemExit(0 if response.status < 500 else 1)
PY
  then ready=1; break; fi
  sleep 0.2
done
[ "$ready" -eq 1 ] || { echo "Backend did not become ready" >&2; exit 1; }

cd "$PROJECT_ROOT/frontend"
nohup env VITE_API_TARGET="$BACKEND_URL" "$NODE" "$VITE" --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort >"$STATE_DIR/frontend.stdout.log" 2>"$STATE_DIR/frontend.stderr.log" &
FRONTEND_PID=$!
sleep 1
kill -0 "$FRONTEND_PID" 2>/dev/null || { cat "$STATE_DIR/frontend.stderr.log" >&2; exit 1; }

cat >"$STATE_FILE" <<EOF
BACKEND_PID=$BACKEND_PID
FRONTEND_PID=$FRONTEND_PID
BACKEND_URL='$BACKEND_URL'
FRONTEND_URL='$FRONTEND_URL'
EOF
trap - INT TERM EXIT
echo "Agent Eval services started successfully."
echo "  Frontend: $FRONTEND_URL (PID $FRONTEND_PID)"
echo "  Backend:  $BACKEND_URL (PID $BACKEND_PID)"
echo "  API docs: $BACKEND_URL/docs"
echo "  Stop:     sh ./stop-all.sh"
