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
import shutil
import sys
from pathlib import Path
from typing import Any

# Common power rails such as 3V3 and 5V are valid net names.
NET_NAME_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]*$")
NET_TYPES = {"ground", "power", "supply", "signal", "analog", "clock", "critical"}


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
        if not isinstance(main, dict):
            problems.append(f"{sid}: main_chip 必须是包含 instance/library_id 的对象")
            main = {}
        if main.get("library_id") not in library_ids:
            problems.append(f"{sid}: 主芯片 {main.get('library_id')} 不在器件库中")
        components = sheet.get("components", [])
        main_components = [comp for comp in components if comp.get("is_main")]
        if len(main_components) != 1:
            problems.append(f"{sid}: 必须且只能有一个 is_main=true 的主器件")
        elif (
            str(main_components[0].get("instance")) != str(main.get("instance"))
            or main_components[0].get("library_id") != main.get("library_id")
        ):
            problems.append(f"{sid}: main_chip 与 components 中的主器件不一致")
        component_by_instance: dict[str, dict] = {}
        endpoint_owners: dict[tuple[str, str], str] = {}
        for comp in components:
            instance = str(comp.get("instance", ""))
            if not instance:
                problems.append(f"{sid}: components 存在空 instance")
            elif instance in instance_ids:
                problems.append(f"{sid}: 位号 {instance} 重复（全板唯一）")
            instance_ids.add(instance)
            component_by_instance[instance] = comp
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
                problems.append(f"{sid}: 网络名 {name!r} 不合法（^[A-Z0-9][A-Z0-9_]*$）")
            if name in net_names:
                problems.append(f"网络 {name} 全局重复定义")
            net_names.add(name)
            net_type = str(net.get("type", ""))
            if net_type not in NET_TYPES:
                problems.append(f"{sid}: 网络 {name} 的 type {net_type!r} 不受支持")
            priority = net.get("priority")
            if not isinstance(priority, int) or not 1 <= priority <= 5:
                problems.append(f"{sid}: 网络 {name} 的 priority 必须是 1-5 整数")
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
                endpoint = (inst, pin_no)
                previous = endpoint_owners.get(endpoint)
                if previous and previous != name:
                    problems.append(
                        f"{sid}: 引脚 {inst}.{pin_no} 同时属于网络 {previous} 和 {name}"
                    )
                endpoint_owners[endpoint] = name

        # Every declared component must be electrically reachable from the main
        # component. This mirrors the layout service's main-domain constraint and
        # catches incomplete LLM-authored sheets before the expensive layout call.
        main_instance = str(main.get("instance", ""))
        adjacency: dict[str, set[str]] = {instance: set() for instance in component_by_instance}
        for net in sheet.get("nets", []):
            members = [str(end.get("instance", "")) for end in net.get("pins", [])]
            for member in members:
                adjacency.setdefault(member, set()).update(other for other in members if other != member)
        reachable: set[str] = set()
        pending = [main_instance] if main_instance else []
        while pending:
            current = pending.pop()
            if current in reachable:
                continue
            reachable.add(current)
            pending.extend(adjacency.get(current, set()) - reachable)
        for instance in component_by_instance:
            if instance not in reachable:
                problems.append(f"{sid}: 器件 {instance} 未通过任何网络连接到主器件 {main_instance}")
        for instance, comp in component_by_instance.items():
            for pin_no in (str(pin) for pin in comp.get("pins", [])):
                if (instance, pin_no) not in endpoint_owners:
                    problems.append(
                        f"{sid}: components 声明的引脚 {instance}.{pin_no} 未出现在任何网络中"
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
    raw_input = args.input
    workspace_input = _workspace_path(raw_input)
    cwd_input = (Path.cwd() / raw_input).resolve() if not raw_input.is_absolute() else raw_input
    # Recover safely when an Agent changed into the installed bundle and wrote
    # its sole model-authored artifact under that cwd. After validation the file
    # is promoted to the canonical evaluation workspace.
    args.input = workspace_input if workspace_input.is_file() else cwd_input
    args.catalog = _workspace_path(args.catalog)

    sheets = _load(args.input)
    catalog = _load(args.catalog)
    devices = catalog.get("devices", catalog if isinstance(catalog, list) else [])
    problems = validate(sheets, devices)
    if problems:
        print("校验不通过，共 %d 处问题:" % len(problems))
        for problem in problems:
            print("  -", problem)
        return 1
    if args.input.resolve() != workspace_input.resolve():
        workspace_input.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.input, workspace_input)
        print(f"已将校验通过的 sheets.json 归一到 workspace: {workspace_input}")
    total = sum(len(s.get("components", [])) for s in sheets["sheets"])
    print(f"校验通过：{len(sheets['sheets'])} 个 sheet，{total} 个器件，"
          f"{len(devices)} 个可用库器件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
