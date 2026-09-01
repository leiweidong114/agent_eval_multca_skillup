#!/usr/bin/env sh
set -eu
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
VERSION="0.9.1"
case "$(uname -m)" in
  x86_64|amd64) ARCH="amd64"; SHA="6a60fa2b5167d30f7a23d5ba1cab327dbbcbd870d872b172db874dc540355db8" ;;
  aarch64|arm64) ARCH="arm64"; SHA="dd5c29ed8a1176f89d2f906cf6e52a9701b0b23f1e0317e884fbe5671bda5975" ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac
TOOLS="$PROJECT_ROOT/.tools/linux"
ARCHIVE="$TOOLS/skill-up_${VERSION}_linux_${ARCH}.tar.gz"
mkdir -p "$TOOLS"
[ -f "$ARCHIVE" ] || curl -fL "https://github.com/alibaba/skill-up/releases/download/v${VERSION}/skill-up_${VERSION}_linux_${ARCH}.tar.gz" -o "$ARCHIVE"
printf '%s  %s\n' "$SHA" "$ARCHIVE" | sha256sum -c -
tar -xzf "$ARCHIVE" -C "$TOOLS"
chmod +x "$TOOLS/skill-up"
