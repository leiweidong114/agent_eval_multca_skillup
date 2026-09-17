#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RELEASE_ROOT=
FORCE=0
SKIP_TESTS=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --release-root) RELEASE_ROOT=${2:?Missing value for --release-root}; shift 2 ;;
    --force) FORCE=1; shift ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$RELEASE_ROOT" ] || { echo "Usage: sh install_linux.sh --release-root DIR [--force] [--skip-tests]" >&2; exit 2; }
RELEASE_ROOT=$(CDPATH= cd -- "$RELEASE_ROOT" && pwd)

BACKEND="$PROJECT_ROOT/backend"
RUNTIME="$BACKEND/.runtime/linux"
TOOLS="$BACKEND/.tools/linux"
CACHE="$BACKEND/.offline-cache/linux"
mkdir -p "$RUNTIME" "$TOOLS" "$CACHE" "$RUNTIME/src" "$RUNTIME/bin"

find_one() {
  found=$(find "$1" -maxdepth 1 -type f -name "$2" | head -n 1)
  [ -n "$found" ] || { echo "Release asset was not found: $1/$2" >&2; exit 1; }
  printf '%s\n' "$found"
}

install_archive() {
  name=$1 archive=$2 target=$3 executable=$4
  if [ -x "$target/$executable" ] && [ "$FORCE" -eq 0 ]; then
    echo "$name already installed: $target"
    return
  fi
  rm -rf "$target" "$target.installing"
  mkdir -p "$target.installing"
  tar -xf "$archive" -C "$target.installing"
  root=$(find "$target.installing" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  [ -n "$root" ] || root="$target.installing"
  mkdir -p "$target"
  cp -a "$root/." "$target/"
  rm -rf "$target.installing"
  [ -x "$target/$executable" ] || { echo "$name executable was not installed" >&2; exit 1; }
}

GO_ARCHIVE=$(find_one "$RELEASE_ROOT/toolchains" 'go*.linux-amd64.tar.gz')
NODE_ARCHIVE=$(find_one "$RELEASE_ROOT/toolchains" 'node*-linux-x64.tar.xz')
PYTHON_ARCHIVE=$(find_one "$RELEASE_ROOT/toolchains" 'python-standalone.tar.gz')
install_archive Go "$GO_ARCHIVE" "$RUNTIME/go" bin/go
install_archive Node "$NODE_ARCHIVE" "$RUNTIME/node" bin/node
install_archive Python "$PYTHON_ARCHIVE" "$RUNTIME/python-base" bin/python3

rm -rf "$RUNTIME/src/skill-up" "$RUNTIME/src/multica"
cp -a "$RELEASE_ROOT/sources/skill-up" "$RUNTIME/src/skill-up"
cp -a "$RELEASE_ROOT/sources/multica" "$RUNTIME/src/multica"
rm -rf "$CACHE/wheelhouse" "$CACHE/npm-cache"
cp -a "$RELEASE_ROOT/python/wheelhouse" "$CACHE/wheelhouse"
cp -a "$RELEASE_ROOT/frontend/npm-cache" "$CACHE/npm-cache"

if [ "$FORCE" -eq 1 ]; then rm -rf "$RUNTIME/python"; fi
if [ ! -x "$RUNTIME/python/bin/python" ]; then
  "$RUNTIME/python-base/bin/python3" -m venv --copies "$RUNTIME/python"
fi
"$RUNTIME/python/bin/python" -m pip install --no-index --find-links "$CACHE/wheelhouse" \
  -e "$BACKEND[dev,web,database,metrics]"

PATH="$RUNTIME/node/bin:$PATH" npm ci --offline --cache "$CACHE/npm-cache" --prefix "$PROJECT_ROOT/frontend"
PATH="$RUNTIME/node/bin:$PATH" npm run build --prefix "$PROJECT_ROOT/frontend"
GO_EXECUTABLE="$RUNTIME/go/bin/go" sh "$PROJECT_ROOT/build_skillup_linux.sh" --test
GO_EXECUTABLE="$RUNTIME/go/bin/go" sh "$PROJECT_ROOT/build_multica_linux.sh" --test

mkdir -p "$TOOLS/justdo"
cp "$RELEASE_ROOT/justdo/JustDo-2026.8.27.AppImage" "$TOOLS/justdo/"
cp "$RELEASE_ROOT/justdo/JustDo-agent-linux-x64" "$TOOLS/justdo/"
chmod +x "$TOOLS/justdo/"*

if [ ! -f "$PROJECT_ROOT/.env" ]; then cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"; fi
if [ "$SKIP_TESTS" -eq 0 ]; then
  "$RUNTIME/python/bin/python" -m pytest "$BACKEND/tests"
fi
"$RUNTIME/python/bin/agent-eval" doctor
echo "LINUX_OFFLINE_INSTALL_OK"
echo "JustDo launcher: $TOOLS/justdo/JustDo-agent-linux-x64"
