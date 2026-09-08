# -*- coding: utf-8 -*-
"""Generate base circuit code + per-group task slices for one sheet.

Base code contains only: Circuit/主芯片模板/所有 sub_circuit 器件模板/所有 Net 定义
（不包含任何 connect）。Slice = 一个功能组（或 MAIN），负责为组内器件参与的网络补
connect 片段。subagent 并行处理各 slice。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _quote(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name))
    cleaned = cleaned.strip("._-")
    return cleaned or "slice"


def pick_slices(sheet: dict) -> list[dict]:
    """Determine the slice assignment for a sheet.

    A slice is created for every group seen in the components list, plus a
    special ``MAIN`` slice that owns main-only nets.  Each net is assigned to
    exactly one slice (deterministic): if it touches any group component, the
    lexicographically-first group wins; otherwise it belongs to ``MAIN``.
    """
    groups: list[str] = []
    for comp in sheet.get("components", []):
        group = str(comp.get("group", "") or "")
        if comp.get("is_main"):
            continue
        if group and group not in groups:
            groups.append(group)
    groups.append("MAIN")
    groups.sort()

    assignment: dict[str, list[dict]] = {group: [] for group in groups}
    components_by_group: dict[str, list[dict]] = {group: [] for group in groups}
    for comp in sheet.get("components", []):
        if comp.get("is_main"):
            components_by_group["MAIN"].append(comp)
        else:
            group = str(comp.get("group", "") or "")
            if group not in components_by_group:
                components_by_group[group] = []
            components_by_group[group].append(comp)

    for net in sheet.get("nets", []):
        involved_groups = set()
        for end in net.get("pins", []):
            inst = str(end.get("instance", ""))
            for group, comps in components_by_group.items():
                if any(str(c.get("instance", "")) == inst for c in comps):
                    involved_groups.add(group)
        involved_groups.discard("MAIN")
        target = sorted(involved_groups)[0] if involved_groups else "MAIN"
        assignment[target].append(net)

    slices = []
    for group in groups:
        slices.append({
            "slice_id": group,
            "file_id": _safe(group),
            "components": components_by_group[group],
            "nets": assignment[group],
        })
    return slices


def build_base_code(sheet: dict, project: str) -> tuple[str, list[dict]]:
    lines: list[str] = []
    sid = sheet["sheet_id"]
    lines.append(f'circuit = Circuit({_quote(project + "_" + sid)})')
    for comp in sheet.get("components", []):
        if comp.get("is_main"):
            pins = ", ".join(_quote(p) for p in comp.get("pins", []))
            lines.append(
                f"circuit.add(Template({_quote(comp['instance'])}, "
                f"{_quote(comp['library_id'])}, {_quote(comp.get('value', ''))}, "
                f"[{pins}]))"
            )
    # group blocks
    seen_groups: list[str] = []
    for comp in sheet.get("components", []):
        group = str(comp.get("group", "") or "")
        if comp.get("is_main") or not group or group in seen_groups:
            continue
        seen_groups.append(group)
        lines.append(f"with circuit.sub_circuit({_quote(group)}):")
        for member in sheet.get("components", []):
            if str(member.get("group", "") or "") == group and not member.get("is_main"):
                pins = ", ".join(_quote(p) for p in member.get("pins", []))
                lines.append(
                    f"    circuit.add(Template({_quote(member['instance'])}, "
                    f"{_quote(member['library_id'])}, "
                    f"{_quote(member.get('value', ''))}, [{pins}]))"
                )
    # net object definitions (no connect)
    lines.append("# ---- 网络对象定义（connect 由各切片代码补充）----")
    net_names: set[str] = set()
    for slice_data in pick_slices(sheet):
        for net in slice_data["nets"]:
            name = net["name"]
            if name in net_names:
                continue
            net_names.add(name)
            lines.append(
                f"circuit.add(Net({_quote(name)}, {_quote(net.get('type', 'signal'))}, "
                f"{int(net.get('priority', 1))}))"
            )
    slices = pick_slices(sheet)
    return "\n".join(lines), slices


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="sheets.json")
    parser.add_argument("--sheet", type=str, required=True)
    parser.add_argument("--fragdir", type=Path, required=True)
    args = parser.parse_args()

    sheets = json.loads(args.input.read_text(encoding="utf-8"))
    project = str(sheets.get("project", "board"))
    sheet = next((s for s in sheets["sheets"] if s["sheet_id"] == args.sheet), None)
    if sheet is None:
        print(f"sheet {args.sheet} 不存在")
        return 1

    base, slices = build_base_code(sheet, project)
    args.fragdir.mkdir(parents=True, exist_ok=True)
    (args.fragdir / "base.txt").write_text(base, encoding="utf-8")
    (args.fragdir / "slices.json").write_text(
        json.dumps({"sheet_id": args.sheet, "slices": slices},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for item in slices:
        target = args.fragdir / f"{item['file_id']}.py"
        if not target.is_file():
            target.write_text(
                "# subagent: 为切片 %s 补充 connect 代码（参考 python_codegen_guide.md）\n"
                % item["slice_id"],
                encoding="utf-8",
            )
    print(f"base + slices written to {args.fragdir}")
    print("slices:", [item["slice_id"] for item in slices])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
