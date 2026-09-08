# -*- coding: utf-8 -*-
"""Merge one sheet's base + slice fragments and run auto_layout over HTTP.

Usage:
    python scripts/layout_sheet.py --input sheets.json --sheet S1 \
        --fragdir out/frags/S1 --out out/layout/S1.json [--iterations 40] [--url ...]

Exit 0 on success. Prints concise result summary. HTTP failures exit 1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL = os.environ.get("AUTOLAYOUT_URL", "http://127.0.0.1:8631")
FRAGMENT_RE = re.compile(r"^[A-Za-z0-9_]+\.py$")


def _fragment_files(fragdir: Path) -> list[Path]:
    files = [p for p in fragdir.glob("*.py") if p.name != "base.txt"]
    files.sort(key=lambda p: p.name)
    return files


def _post_layout(
    code: str, url: str, iterations: int,
    canvas_width: int | None, canvas_height: int | None, canvas_margin: int | None,
    layout_options: dict[str, Any] | None = None,
) -> dict:
    body: dict = {
        "title": "schematic", "code": code, "iterations": iterations,
        "save_result": True, "return_mode": "selected",
    }
    if canvas_width:
        body["canvas_width"] = canvas_width
    if canvas_height:
        body["canvas_height"] = canvas_height
    if canvas_margin is not None:
        body["canvas_margin"] = canvas_margin
    if layout_options:
        body["layout_options"] = layout_options
    body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url + "/api/auto_layout/layout", data=body,
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
        print(f"layout HTTP {exc.code}: {detail[:3000]}")
        raise SystemExit(1) from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--sheet", type=str, required=True)
    parser.add_argument("--fragdir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--canvas-width", type=int, default=None)
    parser.add_argument("--canvas-height", type=int, default=None)
    parser.add_argument("--canvas-margin", type=int, default=None)
    parser.add_argument("--layout-option", action="append", default=None,
                        help="key=value 布局参数，可多次")
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()

    base = (args.fragdir / "base.txt").read_text(encoding="utf-8")
    parts = [base]
    for path in _fragment_files(args.fragdir):
        content = path.read_text(encoding="utf-8")
        if content.strip().startswith("# subagent"):
            continue  # placeholder not yet completed
        parts.append(content)
    code = "\n\n".join(parts)

    # 默认让每个 sub_circuit 功能组成为独立物理区域：实测多个 <5 器件的小组共享
    # 主芯片区域时布局器可能无可行解，而独立成区稳定且美观。
    layout_options: dict[str, Any] = {"subcircuit_min_components": 2}
    for token in args.layout_option or []:
        key, _, value = token.partition("=")
        try:
            layout_options[key] = int(value)
        except ValueError:
            layout_options[key] = value

    payload = _post_layout(code, args.url, args.iterations,
                           args.canvas_width, args.canvas_height, args.canvas_margin,
                           layout_options)
    selected = payload.get("selected")
    if not selected:
        print("服务返回异常：没有 selected 布局")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = selected.get("metrics", {})
    summary = {
        "sheet": args.sheet,
        "layout": str(args.out),
        "component_count": payload.get("component_count"),
        "metrics": {
            key: metrics.get(key) for key in (
                "component_overlap_count", "wire_crossing_count",
                "unrouted_net_count", "wire_length",
            )
        },
        "elapsed_seconds": payload.get("search", {}).get("elapsed_seconds"),
        "result_file": payload.get("result_file"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
