#!/usr/bin/env sh
set -eu

BACKEND_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$BACKEND_ROOT/.." && pwd)
RUNTIME="$BACKEND_ROOT/.runtime/linux"
GO_HOME="$RUNTIME/go"
GO="$GO_HOME/bin/go"
NODE_HOME="$RUNTIME/node"
PYTHON_ENV="$RUNTIME/python"
PYTHON="$PYTHON_ENV/bin/python"
MULTICA_SOURCE="$RUNTIME/src/multica"
SKILLUP_SOURCE="$RUNTIME/src/skill-up"
MULTICA_COMMIT="c1a61e1e863eb62ddd7b5fd5ab5ff85391f212fd"
SKILLUP_COMMIT="80c3147101f81017c66f882b767bdc532de5e74f"
GO_VERSION="1.26.7"
NODE_VERSION="24.18.0"

for tool in git curl tar sha256sum python3; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Required command is missing: $tool" >&2; exit 1; }
done
mkdir -p "$RUNTIME/src" "$RUNTIME/bin"

case "$(uname -m)" in
  x86_64|amd64) GO_ARCH=amd64; NODE_ARCH=x64; GO_SHA=ffb5f8de10c62550dfddab66b36b57030721e0a44a3218e9e1181d7b59f121ca ;;
  aarch64|arm64) GO_ARCH=arm64; NODE_ARCH=arm64; GO_SHA=5a4ec883379d51ee9ce1040d5e87f8d35e20387574dd8c947feb01eabc3c1b37 ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

if [ ! -x "$GO" ]; then
  GO_ARCHIVE="$RUNTIME/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz"
  [ -f "$GO_ARCHIVE" ] || curl -fL "https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz" -o "$GO_ARCHIVE"
  printf '%s  %s\n' "$GO_SHA" "$GO_ARCHIVE" | sha256sum -c -
  rm -rf "$GO_HOME"
  tar -xzf "$GO_ARCHIVE" -C "$RUNTIME"
fi

if [ ! -x "$NODE_HOME/bin/node" ]; then
  NODE_BASENAME="node-v${NODE_VERSION}-linux-${NODE_ARCH}"
  NODE_ARCHIVE="$RUNTIME/${NODE_BASENAME}.tar.xz"
  NODE_SUMS="$RUNTIME/SHASUMS256-node-${NODE_VERSION}.txt"
  [ -f "$NODE_ARCHIVE" ] || curl -fL "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_BASENAME}.tar.xz" -o "$NODE_ARCHIVE"
  [ -f "$NODE_SUMS" ] || curl -fL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o "$NODE_SUMS"
  (cd "$RUNTIME" && grep "  ${NODE_BASENAME}.tar.xz$" "$(basename -- "$NODE_SUMS")" | sha256sum -c -)
  rm -rf "$NODE_HOME" "$RUNTIME/$NODE_BASENAME"
  tar -xJf "$NODE_ARCHIVE" -C "$RUNTIME"
  mv "$RUNTIME/$NODE_BASENAME" "$NODE_HOME"
fi

if [ ! -d "$MULTICA_SOURCE/.git" ]; then
  git clone --depth 1 --branch v0.4.36 https://github.com/multica-ai/multica.git "$MULTICA_SOURCE"
fi
[ "$(git -C "$MULTICA_SOURCE" rev-parse HEAD)" = "$MULTICA_COMMIT" ] || {
  echo "Unexpected Multica source commit" >&2; exit 1;
}

if [ ! -d "$SKILLUP_SOURCE/.git" ]; then
  git clone --depth 1 --branch v0.9.1 https://github.com/alibaba/skill-up.git "$SKILLUP_SOURCE"
fi
[ "$(git -C "$SKILLUP_SOURCE" rev-parse HEAD)" = "$SKILLUP_COMMIT" ] || {
  echo "Unexpected Skill-Up source commit" >&2; exit 1;
}

GO_EXECUTABLE="$GO" sh "$PROJECT_ROOT/build_skillup_linux.sh" --test
GO_EXECUTABLE="$GO" sh "$PROJECT_ROOT/build_multica_linux.sh" --test

if [ ! -x "$PYTHON" ]; then
  python3 -m venv --copies "$PYTHON_ENV"
fi
"$PYTHON" -m pip install -e "$BACKEND_ROOT[dev,web,database,metrics]"

PATH="$NODE_HOME/bin:$PATH" NPM_EXECUTABLE="$NODE_HOME/bin/npm" \
  sh "$PROJECT_ROOT/build_frontend_linux.sh"

if [ ! -f "$PROJECT_ROOT/.env" ]; then
  cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
  echo "Created .env from .env.example; add private credentials before online model tests."
fi

"$PYTHON" -m pytest "$BACKEND_ROOT/tests"
"$PYTHON_ENV/bin/agent-eval" doctor
echo "LINUX_SETUP_OK"
echo "Start services: sh ./start-all.sh"
