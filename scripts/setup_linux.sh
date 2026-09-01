#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
RUNTIME="$PROJECT_ROOT/.runtime/linux"
BIN="$RUNTIME/bin"
GO_HOME="$RUNTIME/go"
GO="$GO_HOME/bin/go"
PYTHON_ENV="$RUNTIME/python"
PYTHON="$PYTHON_ENV/bin/python"
MULTICA_SOURCE="$RUNTIME/src/multica"
MULTICA_COMMIT="c1a61e1e863eb62ddd7b5fd5ab5ff85391f212fd"
mkdir -p "$RUNTIME" "$BIN" "$RUNTIME/src"

if [ ! -x "$GO" ]; then
  GO_VERSION="1.26.7"
  case "$(uname -m)" in
    x86_64|amd64) ARCH="amd64"; SHA="ffb5f8de10c62550dfddab66b36b57030721e0a44a3218e9e1181d7b59f121ca" ;;
    aarch64|arm64) ARCH="arm64"; SHA="5a4ec883379d51ee9ce1040d5e87f8d35e20387574dd8c947feb01eabc3c1b37" ;;
    *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
  esac
  ARCHIVE="$RUNTIME/go${GO_VERSION}.linux-${ARCH}.tar.gz"
  [ -f "$ARCHIVE" ] || curl -fL "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz" -o "$ARCHIVE"
  printf '%s  %s\n' "$SHA" "$ARCHIVE" | sha256sum -c -
  tar -xzf "$ARCHIVE" -C "$RUNTIME"
fi

if [ ! -d "$MULTICA_SOURCE/.git" ]; then
  git clone --depth 1 --branch v0.4.36 https://github.com/multica-ai/multica.git "$MULTICA_SOURCE"
fi
ACTUAL_COMMIT="$(git -C "$MULTICA_SOURCE" rev-parse HEAD)"
[ "$ACTUAL_COMMIT" = "$MULTICA_COMMIT" ] || { echo "Unexpected Multica source commit: $ACTUAL_COMMIT" >&2; exit 1; }
COMMAND_DIR="$MULTICA_SOURCE/server/cmd/multica-eval-runtime"
mkdir -p "$COMMAND_DIR"
cp runtime/multica-local-runner/main.go runtime/multica-local-runner/main_test.go "$COMMAND_DIR/"
(cd "$MULTICA_SOURCE/server" && "$GO" build -trimpath -o "$BIN/multica-eval-runtime" ./cmd/multica-eval-runtime)
(cd "$MULTICA_SOURCE/server" && "$GO" test ./cmd/multica-eval-runtime)
chmod +x "$BIN/multica-eval-runtime"

[ -x .tools/linux/skill-up ] || sh scripts/install_skillup_linux.sh
if [ ! -x "$PYTHON" ]; then
  python3 -m venv --copies "$PYTHON_ENV"
fi
"$PYTHON" -m pip install -e ".[dev]"
"$PYTHON" -m pytest
"$PYTHON" -m agent_eval.cli doctor
echo "LINUX_SETUP_OK"
