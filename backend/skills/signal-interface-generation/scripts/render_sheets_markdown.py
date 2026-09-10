# -*- coding: utf-8 -*-
"""Render deterministic, human-readable Markdown pages from sheets.json."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _workspace_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    for parent in Path(__file__).resolve().parents:
        if parent.name == ".agents":
            workspace = parent.parent
            if "out" in path.parts:
                return workspace.joinpath(*path.parts[path.parts.index("out") :])
            return workspace / path
    return Path.cwd() / path


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-") or "sheet"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.input = _workspace_path(args.input)
    args.out_dir = _workspace_path(args.out_dir)

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    sheets = payload.get("sheets", [])
    if not sheets:
        print("sheets.json 没有可渲染的 sheets")
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for sheet in sheets:
        sid = str(sheet.get("sheet_id", "sheet"))
        title = str(sheet.get("title", sid))
        lines = [f"# {title}", "", "## 器件清单", "",
                 "| 位号 | library_id | 值 | 功能组 | 主器件 |",
                 "|---|---|---|---|---|"]
        for component in sheet.get("components", []):
            lines.append(
                "| {instance} | {library_id} | {value} | {group} | {main} |".format(
                    instance=component.get("instance", ""),
                    library_id=component.get("library_id", ""),
                    value=component.get("value", ""),
                    group=component.get("group", ""),
                    main="是" if component.get("is_main") else "否",
                )
            )
        lines.extend(["", "## 信号接口", "",
                      "| 输入器件 | 输入引脚号 | 输出器件 | 输出引脚号 | 网络名 | 说明 |",
                      "|---|---|---|---|---|---|"])
        for net in sheet.get("nets", []):
            endpoints = net.get("pins", [])
            if len(endpoints) == 1:
                endpoints = [endpoints[0], {"instance": "跨页标签", "pin": "-"}]
            for destination in endpoints[1:]:
                source = endpoints[0]
                lines.append(
                    f"| {source.get('instance', '')} | {source.get('pin', '')} | "
                    f"{destination.get('instance', '')} | {destination.get('pin', '')} | "
                    f"{net.get('name', '')} | {net.get('type', '')}, priority={net.get('priority', '')} |"
                )
        target = args.out_dir / f"{_safe(sid)}_{_safe(title)}.md"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"markdown written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
