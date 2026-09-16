#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND="$PROJECT_ROOT/backend"
RUNTIME="$BACKEND/.runtime/linux"
GO=${GO_EXECUTABLE:-"$RUNTIME/go/bin/go"}
SOURCE="$RUNTIME/src/multica"
SERVER="$SOURCE/server"
OUTPUT="$RUNTIME/bin/multica-eval-runtime"
PROXY_OUTPUT="$RUNTIME/bin/justdo-http-agent"

[ -x "$GO" ] || { echo "Go was not found: $GO" >&2; exit 1; }
[ -f "$SERVER/go.mod" ] || { echo "Multica source was not found: $SERVER" >&2; exit 1; }

for patch in \
  "$BACKEND/patches/multica-openclaw-agent-exec.patch" \
  "$BACKEND/patches/multica-claude-terminal-exit.patch"
do
  if git -C "$SERVER" apply --check "$patch" 2>/dev/null; then
    git -C "$SERVER" apply "$patch"
  elif ! git -C "$SERVER" apply --reverse --check "$patch" 2>/dev/null; then
    echo "Bundled Multica source is incompatible with patch: $patch" >&2
    exit 1
  fi
done

TARGET="$SERVER/cmd/multica-eval-runtime"
mkdir -p "$TARGET" "$RUNTIME/bin"
cp "$BACKEND/runtime/multica-local-runner/main.go" "$TARGET/main.go"
cp "$BACKEND/runtime/multica-local-runner/main_test.go" "$TARGET/main_test.go"

MOD_FLAG=
[ -d "$SERVER/vendor" ] && MOD_FLAG=-mod=vendor
(cd "$SERVER" && "$GO" build $MOD_FLAG -trimpath -o "$OUTPUT.new" ./cmd/multica-eval-runtime)
(cd "$BACKEND/runtime/justdo-http-agent" && "$GO" build -trimpath -o "$PROXY_OUTPUT.new" ./main.go)
mv "$OUTPUT.new" "$OUTPUT"
mv "$PROXY_OUTPUT.new" "$PROXY_OUTPUT"
chmod +x "$OUTPUT" "$PROXY_OUTPUT"

if [ "${1:-}" = "--test" ]; then
  (cd "$SERVER" && "$GO" test $MOD_FLAG ./cmd/multica-eval-runtime)
  (cd "$BACKEND/runtime/justdo-http-agent" && "$GO" test main.go main_test.go)
fi

echo "MULTICA_BUILD_OK: $OUTPUT"
echo "JUSTDO_HTTP_PROXY_BUILD_OK: $PROXY_OUTPUT"
