# -*- coding: utf-8 -*-
"""Merge one sheet's base + slice fragments and run auto_layout over HTTP.

Usage:
    python scripts/layout_sheet.py --input sheets.json --sheet S1 \
        --fragdir out/frags/S1 --out out/layout/S1.json [--iterations 40] [--url ...]

Exit 0 on success. Prints concise result summary. HTTP failures exit 1.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL = os.environ.get("AUTOLAYOUT_URL", "http://127.0.0.1:8631")
FRAGMENT_RE = re.compile(r"^[A-Za-z0-9_]+\.py$")


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


def _circuit_network_name(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "circuit"
    ):
        return node.attr
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "circuit"
        and node.func.attr == "__getattr__"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value
    return None


def _fragment_files(fragdir: Path) -> list[Path]:
    slices_path = fragdir / "slices.json"
    if not slices_path.is_file():
        print(f"切片清单不存在: {slices_path}")
        raise SystemExit(1)
    slices = json.loads(slices_path.read_text(encoding="utf-8")).get("slices", [])
    files: list[Path] = []
    problems: list[str] = []
    forbidden = {"Circuit", "Template", "Net"}
    for item in slices:
        path = fragdir / f"{item['file_id']}.py"
        if not path.is_file():
            problems.append(f"缺少切片文件 {path.name}")
            continue
        content = path.read_text(encoding="utf-8")
        if content.strip().startswith("# subagent"):
            problems.append(f"切片 {path.name} 仍是占位内容，subagent 未完成")
            continue
        try:
            tree = ast.parse(content, filename=str(path))
        except SyntaxError as exc:
            problems.append(f"切片 {path.name} Python 语法错误: {exc}")
            continue
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        illegal = sorted(names & forbidden)
        if illegal:
            problems.append(f"切片 {path.name} 重复定义了禁止对象: {', '.join(illegal)}")
        connect_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "connect"
        ]
        if item.get("nets") and not connect_calls:
            problems.append(f"切片 {path.name} 有网络但没有 connect(...) 调用")
        expected = {
            str(net["name"]): {
                (str(endpoint["instance"]), str(endpoint["pin"]))
                for endpoint in net.get("pins", [])
            }
            for net in item.get("nets", [])
        }
        actual: dict[str, set[tuple[str, str]]] = {}
        for call in connect_calls:
            if len(call.args) < 2:
                problems.append(f"切片 {path.name} 存在参数不足的 connect 调用")
                continue
            net_node = call.args[1]
            net_name = _circuit_network_name(net_node)
            if net_name is None:
                problems.append(
                    f"切片 {path.name} 的 connect 网络必须写成 circuit.<网络名>；"
                    "数字开头的网络使用 circuit.__getattr__(\"3V3\")"
                )
                continue
            if net_name in actual:
                problems.append(f"切片 {path.name} 重复 connect 网络 {net_name}")
                continue
            pin_nodes = (
                call.args[0].elts
                if isinstance(call.args[0], (ast.List, ast.Tuple))
                else [call.args[0]]
            )
            endpoints: set[tuple[str, str]] = set()
            for pin_call in pin_nodes:
                valid_pin = (
                    isinstance(pin_call, ast.Call)
                    and isinstance(pin_call.func, ast.Name)
                    and pin_call.func.id == "pin"
                    and len(pin_call.args) >= 2
                    and isinstance(pin_call.args[0], ast.Attribute)
                    and isinstance(pin_call.args[0].value, ast.Name)
                    and pin_call.args[0].value.id == "circuit"
                    and isinstance(pin_call.args[1], ast.Constant)
                )
                if not valid_pin:
                    problems.append(
                        f"切片 {path.name} 网络 {net_name} 的引脚必须写成 pin(circuit.<位号>, <常量引脚号>)"
                    )
                    continue
                endpoints.add((pin_call.args[0].attr, str(pin_call.args[1].value)))
            actual[net_name] = endpoints
        if set(actual) != set(expected):
            problems.append(
                f"切片 {path.name} 网络集合不匹配: expected={sorted(expected)}, actual={sorted(actual)}"
            )
        for net_name in sorted(set(actual) & set(expected)):
            if actual[net_name] != expected[net_name]:
                problems.append(
                    f"切片 {path.name} 网络 {net_name} 端点不匹配: "
                    f"expected={sorted(expected[net_name])}, actual={sorted(actual[net_name])}"
                )
        files.append(path)
    if problems:
        print("切片完整性校验失败:")
        for problem in problems:
            print("  -", problem)
        raise SystemExit(1)
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
    args.input = _workspace_path(args.input)
    raw_fragdir = args.fragdir
    workspace_fragdir = _workspace_path(raw_fragdir)
    cwd_fragdir = (
        (Path.cwd() / raw_fragdir).resolve()
        if not raw_fragdir.is_absolute() else raw_fragdir
    )
    if cwd_fragdir.is_dir() and cwd_fragdir.resolve() != workspace_fragdir.resolve():
        workspace_fragdir.mkdir(parents=True, exist_ok=True)
        for local_fragment in cwd_fragdir.glob("*.py"):
            content = local_fragment.read_text(encoding="utf-8")
            if not content.strip().startswith("# subagent"):
                shutil.copy2(local_fragment, workspace_fragdir / local_fragment.name)
                print(f"已归一切片到 workspace: {local_fragment.name}")
    args.fragdir = workspace_fragdir
    args.out = _workspace_path(args.out)

    base = (args.fragdir / "base.txt").read_text(encoding="utf-8")
    parts = [base]
    for path in _fragment_files(args.fragdir):
        content = path.read_text(encoding="utf-8")
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
