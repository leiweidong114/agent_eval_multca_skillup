#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUTPUT_ROOT=${1:?Usage: sh prepare_linux_release.sh OUTPUT_ROOT [ARCHIVE_PATH]}
ARCHIVE_PATH=${2:-}
RUNTIME="$PROJECT_ROOT/backend/.runtime/linux"
JUSTDO_RELEASE=${JUSTDO_LINUX_RELEASE_ROOT:-"$PROJECT_ROOT/../JustDo/release-linux-2026.8.27"}

require_file() {
  [ -f "$1" ] || { echo "Required file was not found: $1" >&2; exit 1; }
}
require_dir() {
  [ -d "$1" ] || { echo "Required directory was not found: $1" >&2; exit 1; }
}

[ ! -e "$OUTPUT_ROOT" ] || { echo "Output path already exists: $OUTPUT_ROOT" >&2; exit 1; }
require_file "$RUNTIME/go1.26.7.linux-amd64.tar.gz"
require_file "$RUNTIME/node-v24.18.0-linux-x64.tar.xz"
require_file "$RUNTIME/python-standalone.tar.gz"
require_dir "$RUNTIME/release-wheelhouse"
require_dir "$RUNTIME/release-npm-cache"
require_dir "$RUNTIME/src/skill-up"
require_dir "$RUNTIME/src/multica"
require_file "$JUSTDO_RELEASE/JustDo-2026.8.27.AppImage"
require_file "$JUSTDO_RELEASE/JustDo-agent-linux-x64"

mkdir -p \
  "$OUTPUT_ROOT/toolchains" \
  "$OUTPUT_ROOT/python/wheelhouse" \
  "$OUTPUT_ROOT/frontend/npm-cache" \
  "$OUTPUT_ROOT/sources/skill-up" \
  "$OUTPUT_ROOT/sources/multica" \
  "$OUTPUT_ROOT/justdo"

cp "$RUNTIME/go1.26.7.linux-amd64.tar.gz" "$OUTPUT_ROOT/toolchains/"
cp "$RUNTIME/node-v24.18.0-linux-x64.tar.xz" "$OUTPUT_ROOT/toolchains/"
cp "$RUNTIME/python-standalone.tar.gz" "$OUTPUT_ROOT/toolchains/"
cp -a "$RUNTIME/release-wheelhouse/." "$OUTPUT_ROOT/python/wheelhouse/"
cp -a "$RUNTIME/release-npm-cache/." "$OUTPUT_ROOT/frontend/npm-cache/"
cp -a "$RUNTIME/src/skill-up/." "$OUTPUT_ROOT/sources/skill-up/"
cp -a "$RUNTIME/src/multica/." "$OUTPUT_ROOT/sources/multica/"
rm -rf "$OUTPUT_ROOT/sources/skill-up/.git" "$OUTPUT_ROOT/sources/multica/.git"
cp "$JUSTDO_RELEASE/JustDo-2026.8.27.AppImage" "$OUTPUT_ROOT/justdo/"
cp "$JUSTDO_RELEASE/JustDo-agent-linux-x64" "$OUTPUT_ROOT/justdo/"
cp "$PROJECT_ROOT/install_linux.sh" "$OUTPUT_ROOT/"

(cd "$OUTPUT_ROOT" && find . -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS.txt)

if [ -n "$ARCHIVE_PATH" ]; then
  mkdir -p "$(dirname -- "$ARCHIVE_PATH")"
  tar -C "$(dirname -- "$OUTPUT_ROOT")" -czf "$ARCHIVE_PATH" "$(basename -- "$OUTPUT_ROOT")"
  echo "LINUX_RELEASE_ARCHIVE_READY: $ARCHIVE_PATH"
fi
echo "LINUX_RELEASE_DIRECTORY_READY: $OUTPUT_ROOT"
