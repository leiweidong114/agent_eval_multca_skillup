# -*- coding: utf-8 -*-
"""Fetch the auto_layout device catalogue (and per-device pins) from the service.

Usage:
    python scripts/fetch_catalog.py --out catalog.json
Env:
    AUTOLAYOUT_URL   default http://127.0.0.1:8631
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = os.environ.get("AUTOLAYOUT_URL", "http://127.0.0.1:8631")


def _workspace_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    for parent in Path(__file__).resolve().parents:
        if parent.name == ".agents":
            workspace = parent.parent
            # Agents occasionally run a bundled script from its own directory and
            # prefix an artifact path with several ``..`` components.  Artifacts in
            # this pipeline are rooted at ``out/``; keep that suffix inside the
            # evaluation workspace instead of allowing it to escape the sandbox.
            if "out" in path.parts:
                return workspace.joinpath(*path.parts[path.parts.index("out") :])
            return workspace / path
    return Path.cwd() / path


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--with-pins", action="store_true", help="fetch pin lists too")
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    args.out = _workspace_path(args.out)

    try:
        payload = _get(args.url + "/api/auto_layout/devices")
    except urllib.error.HTTPError as exc:
        print(f"catalog fetch failed (HTTP {exc.code}): {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"catalog fetch failed, is auto_layout service running at {args.url}? {exc}")
        return 2

    devices = payload["devices"]
    failed: list[str] = []
    if args.with_pins:
        for device in devices:
            library_id = urllib.parse.quote(device["library_id"], safe="")
            try:
                pins = _get(args.url + "/api/auto_layout/devices/" + library_id + "/pins")
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{device['library_id']}: {exc}")
                pins = {"pins": []}
            device["pins"] = pins.get("pins", [])
        if failed:
            print("WARNING: 以下器件引脚抓取失败:")
            for item in failed:
                print("  -", item)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"catalog written: {args.out} ({len(devices)} devices)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
