# -*- coding: utf-8 -*-
"""Render layout JSON files into a multi-sheet web page via Service 2.

Usage:
    python scripts/apply.py --title "..." --sheet "标题|path.json" [--sheet ...] --out out.json
    python scripts/apply.py --title "..." --layout-dir out/layout --out out.json
Env:
    AUTOLAYOUT_URL  default http://127.0.0.1:8631
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = os.environ.get("AUTOLAYOUT_URL", "http://127.0.0.1:8631")


def _post(url: str, body: dict) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url + "/api/apply_schematic/render", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("detail", raw)
        except json.JSONDecodeError:
            detail = raw
        print(f"apply HTTP {exc.code}: {detail[:3000]}")
        raise SystemExit(1) from exc


def _collect_sheets(args: argparse.Namespace) -> list[dict]:
    sheets: list[dict] = []
    if args.layout_dir:
        for path in sorted(Path(args.layout_dir).glob("*.json")):
            sheets.append({"title": path.stem, "path": str(path.resolve())})
    for token in args.sheet or []:
        if "|" in token:
            title, path = token.split("|", 1)
        else:
            title, path = Path(token).stem, token
        sheets.append({"title": title, "path": str(Path(path).resolve())})
    if not sheets:
        print("没有收集到任何布局 JSON（用 --sheet 或 --layout-dir）")
        raise SystemExit(1)
    return sheets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="原理图")
    parser.add_argument("--sheet", action="append",
                        help="图页，格式：标题|JSON路径，可重复")
    parser.add_argument("--layout-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()

    sheets = _collect_sheets(args)
    payload = _post(args.url, {"title": args.title, "sheets": sheets})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "url": payload["url"],
        "project_id": payload["project_id"],
        "sheet_count": payload["sheet_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
