# -*- coding: utf-8 -*-
"""Validate a signal-interface ``sheets.json`` against the device catalogue.

Usage:
    python scripts/validate_sheets.py --input sheets.json --catalog catalog.json
Exit code 0 = valid; otherwise prints problems and returns 1.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

NET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _pin_numbers(devices: list[dict], library_id: str) -> set[str]:
    for device in devices:
        if device["library_id"] == library_id:
            return {str(pin["number"]) for pin in device.get("pins", [])}
    return set()


def validate(sheets: dict, devices: list[dict]) -> list[str]:
    problems: list[str] = []
    instance_ids: set[str] = set()
    net_names: set[str] = set()
    library_ids = {d["library_id"] for d in devices}

    for sheet in sheets.get("sheets", []):
        sid = str(sheet.get("sheet_id", "?"))
        main = sheet.get("main_chip") or {}
        if main.get("library_id") not in library_ids:
            problems.append(f"{sid}: 主芯片 {main.get('library_id')} 不在器件库中")
        for comp in sheet.get("components", []):
            instance = str(comp.get("instance", ""))
            if not instance:
                problems.append(f"{sid}: components 存在空 instance")
            elif instance in instance_ids:
                problems.append(f"{sid}: 位号 {instance} 重复（全板唯一）")
            instance_ids.add(instance)
            library_id = comp.get("library_id")
            if library_id not in library_ids:
                problems.append(f"{sid}: 器件 {instance} 的 {library_id} 不在器件库中")
                continue
            known = _pin_numbers(devices, library_id)
            if not comp.get("is_main") and not str(comp.get("group", "")).strip():
                problems.append(f"{sid}: 非主器件 {instance} 缺少 group")
            for pin in comp.get("pins", []):
                if str(pin) not in known:
                    problems.append(f"{sid}: {instance} 引用引脚 {pin}，但 {library_id} 无此引脚号")

        for net in sheet.get("nets", []):
            name = str(net.get("name", ""))
            if not NET_NAME_RE.match(name):
                problems.append(f"{sid}: 网络名 {name!r} 不合法（^[A-Z][A-Z0-9_]*$）")
            if name in net_names:
                problems.append(f"网络 {name} 全局重复定义")
            net_names.add(name)
            pins = net.get("pins", [])
            if len(pins) < 1:
                problems.append(f"{sid}: 网络 {name} 没有端点")
            for end in pins:
                inst = str(end.get("instance", ""))
                pin_no = str(end.get("pin", ""))
                found = next(
                    (c for c in sheet.get("components", []) if str(c.get("instance", "")) == inst),
                    None,
                )
                if found is None:
                    problems.append(f"{sid}: 网络 {name} 引用未声明的器件 {inst}")
                    continue
                if pin_no not in [str(p) for p in found.get("pins", [])]:
                    problems.append(
                        f"{sid}: 网络 {name} 的 {inst}.{pin_no} 未列入 components[].pins"
                    )

    if not sheets.get("sheets"):
        problems.append("sheets.json 没有 sheets 数组")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True,
                        help="fetch_catalog.py 输出（须带 --with-pins）")
    args = parser.parse_args()

    sheets = _load(args.input)
    catalog = _load(args.catalog)
    devices = catalog.get("devices", catalog if isinstance(catalog, list) else [])
    problems = validate(sheets, devices)
    if problems:
        print("校验不通过，共 %d 处问题:" % len(problems))
        for problem in problems:
            print("  -", problem)
        return 1
    total = sum(len(s.get("components", [])) for s in sheets["sheets"])
    print(f"校验通过：{len(sheets['sheets'])} 个 sheet，{total} 个器件，"
          f"{len(devices)} 个可用库器件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
