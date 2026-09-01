#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def judge(input_path: Path, output: Path | None = None, schematic_path: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    required = ["01_signal_interfaces.json", "02_cbb_classification.json", "schematic.json", "events.json", "manifest.json"]
    present = [name for name in required if output is not None and (output / name).is_file()]
    diagram = load(input_path)
    candidate = schematic_path or (output / "schematic.json" if output is not None else None)
    if candidate is None or not candidate.is_file():
        return {"score": 0, "passed": False, "errors": ["schematic.json is missing"], "dimensions": {}}
    schematic = load(candidate)
    expected_components = {c["id"]: c for c in diagram.get("components", [])}
    actual_components = {c.get("id"): c for c in schematic.get("components", [])}
    component_ratio = len(expected_components.keys() & actual_components.keys()) / max(1, len(expected_components))
    component_score = round(25 * component_ratio, 2)
    if expected_components.keys() != actual_components.keys():
        errors.append("component IDs differ from input")
    pin_total = pin_hits = 0
    for component_id, expected in expected_components.items():
        expected_pins = {(p["id"], p["direction"]) for p in expected.get("pins", [])}
        actual_pins = {(p.get("id"), p.get("direction")) for p in actual_components.get(component_id, {}).get("pins", [])}
        pin_total += len(expected_pins)
        pin_hits += len(expected_pins & actual_pins)
    pin_score = round(15 * pin_hits / max(1, pin_total), 2)
    if pin_hits != pin_total:
        errors.append("pin IDs or directions differ from input")
    def signature(wire: dict[str, Any]) -> tuple[str, str, str, str, str]:
        return (wire.get("source", {}).get("component"), wire.get("source", {}).get("pin"),
                wire.get("target", {}).get("component"), wire.get("target", {}).get("pin"), wire.get("net"))
    expected_wires = {signature(w) for w in diagram.get("connections", [])}
    actual_wires = {signature(w) for w in schematic.get("wires", [])}
    topology_score = round(40 * len(expected_wires & actual_wires) / max(1, len(expected_wires)), 2)
    if expected_wires != actual_wires:
        errors.append("wire topology differs from input")
    expected_nets = {w.get("net") for w in diagram.get("connections", [])}
    actual_nets = set(schematic.get("nets", []))
    net_score = round(15 * len(expected_nets & actual_nets) / max(1, len(expected_nets)), 2)
    if expected_nets != actual_nets:
        errors.append("net names differ from input")
    component_files = sum(output is not None and (output / "components" / f"{component_id}.json").is_file() for component_id in expected_components)
    package_ratio = (
        int(schematic.get("schema") == "tianshu-schematic/v1")
        if schematic_path is not None
        else (len(present) + component_files) / max(1, len(required) + len(expected_components))
    )
    packaging_score = round(5 * package_ratio, 2)
    score = round(component_score + pin_score + topology_score + net_score + packaging_score, 2)
    return {
        "score": score, "passed": score == 100 and not errors, "errors": errors,
        "dimensions": {"components": component_score, "pins": pin_score, "topology": topology_score,
                       "nets": net_score, "artifacts": packaging_score},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--schematic", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = judge(args.input, args.output, args.schematic)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
