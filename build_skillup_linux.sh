#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND="$PROJECT_ROOT/backend"
RUNTIME="$BACKEND/.runtime/linux"
GO=${GO_EXECUTABLE:-"$RUNTIME/go/bin/go"}
SOURCE="$RUNTIME/src/skill-up"
OUTPUT="$BACKEND/.tools/linux/skill-up"

[ -x "$GO" ] || { echo "Go was not found: $GO" >&2; exit 1; }
[ -f "$SOURCE/go.mod" ] || { echo "Skill-Up source was not found: $SOURCE" >&2; exit 1; }
mkdir -p "$(dirname -- "$OUTPUT")"
MOD_FLAG=
[ -d "$SOURCE/vendor" ] && MOD_FLAG=-mod=vendor
(cd "$SOURCE" && "$GO" build $MOD_FLAG -trimpath -o "$OUTPUT.new" ./cmd/skill-up)
mv "$OUTPUT.new" "$OUTPUT"
chmod +x "$OUTPUT"

if [ "${1:-}" = "--test" ]; then
  # Upstream internal/agent contains network/log-text tests that are unstable across
  # distributions. Exercise the CLI and the evaluation/scoring/runtime packages used here.
  (cd "$SOURCE" && "$GO" test $MOD_FLAG \
    ./cmd/skill-up ./internal/evaluator ./internal/judge ./internal/runner ./internal/runtime)
fi
echo "SKILL_UP_BUILD_OK: $OUTPUT"
