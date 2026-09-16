#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
NODE_ROOT="$PROJECT_ROOT/backend/.runtime/linux/node"
NPM=${NPM_EXECUTABLE:-"$NODE_ROOT/bin/npm"}
[ -x "$NPM" ] || NPM=$(command -v npm || true)
[ -n "$NPM" ] || { echo "npm was not found; run backend/scripts/setup_linux.sh first" >&2; exit 1; }
cd "$PROJECT_ROOT/frontend"
[ -d node_modules ] || "$NPM" ci
"$NPM" run build
echo "FRONTEND_BUILD_OK"
