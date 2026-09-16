#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE_FILE="$PROJECT_ROOT/backend/.runtime/service-manager-linux/services.env"
if [ ! -f "$STATE_FILE" ]; then
  echo "No Linux services started by start-all.sh were found."
  exit 0
fi
# shellcheck disable=SC1090
. "$STATE_FILE"
stop_managed_process() {
  pid=$1
  marker=$2
  case "$pid" in ''|*[!0-9]*) return 0 ;; esac
  [ -r "/proc/$pid/cmdline" ] || return 0
  command_line=$(tr '\000' ' ' <"/proc/$pid/cmdline")
  case "$command_line" in
    *"$marker"*) kill "$pid" 2>/dev/null || true ;;
    *) echo "PID $pid no longer matches $marker; leaving it running." >&2 ;;
  esac
}
stop_managed_process "${FRONTEND_PID:-}" "vite.js"
stop_managed_process "${BACKEND_PID:-}" "run_server.py"
rm -f "$STATE_FILE"
echo "Agent Eval Linux services stopped."
