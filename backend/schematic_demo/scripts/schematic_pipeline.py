#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


VALID_DIRECTIONS = {"input", "output", "bidirectional", "power"}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_diagram(diagram: dict[str, Any]) -> None:
    components = diagram.get("components")
    connections = diagram.get("connections")
    if not isinstance(components, list) or not components:
        raise ValueError("components must be a non-empty array")
    if not isinstance(connections, list):
        raise ValueError("connections must be an array")
    component_ids: set[str] = set()
    pin_map: dict[str, set[str]] = {}
    for component in components:
        component_id = str(component.get("id", "")).strip()
        if not component_id or component_id in component_ids:
            raise ValueError(f"invalid or duplicate component id: {component_id}")
        component_ids.add(component_id)
        if component.get("library_type") not in {"public", "private"}:
            raise ValueError(f"{component_id}: library_type must be public or private")
        pins = component.get("pins")
        if not isinstance(pins, list) or not pins:
            raise ValueError(f"{component_id}: pins must be a non-empty array")
        pin_ids: set[str] = set()
        for pin in pins:
            pin_id = str(pin.get("id", "")).strip()
            if not pin_id or pin_id in pin_ids:
                raise ValueError(f"{component_id}: invalid or duplicate pin id {pin_id}")
            if pin.get("direction") not in VALID_DIRECTIONS:
                raise ValueError(f"{component_id}.{pin_id}: invalid direction")
            pin_ids.add(pin_id)
        pin_map[component_id] = pin_ids
    connection_ids: set[str] = set()
    for connection in connections:
        connection_id = str(connection.get("id", "")).strip()
        if not connection_id or connection_id in connection_ids:
            raise ValueError(f"invalid or duplicate connection id: {connection_id}")
        connection_ids.add(connection_id)
        if not str(connection.get("net", "")).strip():
            raise ValueError(f"{connection_id}: net is required")
        for endpoint_name in ("source", "target"):
            endpoint = connection.get(endpoint_name) or {}
            component_id, pin_id = endpoint.get("component"), endpoint.get("pin")
            if component_id not in pin_map or pin_id not in pin_map[component_id]:
                raise ValueError(f"{connection_id}: invalid {endpoint_name} {component_id}.{pin_id}")


def build_interfaces(diagram: dict[str, Any]) -> list[dict[str, Any]]:
    nets: dict[tuple[str, str], set[str]] = {}
    for wire in diagram["connections"]:
        for key in ("source", "target"):
            endpoint = wire[key]
            nets.setdefault((endpoint["component"], endpoint["pin"]), set()).add(wire["net"])
    blocks = []
    for component in diagram["components"]:
        grouped = {key: [] for key in VALID_DIRECTIONS}
        for pin in component["pins"]:
            item = {**pin, "nets": sorted(nets.get((component["id"], pin["id"]), set()))}
            grouped[pin["direction"]].append(item)
        blocks.append({
            "component_id": component["id"], "component_name": component["name"],
            "component_type": component["type"], "inputs": grouped["input"],
            "outputs": grouped["output"], "bidirectional": grouped["bidirectional"],
            "power": grouped["power"],
        })
    return blocks


def generate_component(component: dict[str, Any], interface: dict[str, Any], index: int) -> dict[str, Any]:
    is_public = component["library_type"] == "public"
    return {
        "id": component["id"], "name": component["name"], "type": component["type"],
        "library_type": component["library_type"],
        "generation": {
            "strategy": "reference_template_patch" if is_public else "interface_driven_synthesis",
            "reference": f"public-cbb/{component['type']}.json" if is_public else None,
        },
        "symbol": {"shape": "rect", "width": 150, "height": max(90, 30 + 22 * len(component["pins"]))},
        "position": {"x": 80 + (index % 3) * 300, "y": 70 + (index // 3) * 240},
        "pins": component["pins"], "interface": interface,
    }


def run_pipeline(diagram: dict[str, Any], output: Path) -> dict[str, Any]:
    validate_diagram(diagram)
    output.mkdir(parents=True, exist_ok=True)
    interfaces = build_interfaces(diagram)
    write_json(output / "01_signal_interfaces.json", {"blocks": interfaces})
    classifications = [{
        "component_id": item["id"], "library_type": item["library_type"],
        "route": "public_reference" if item["library_type"] == "public" else "private_generate",
    } for item in diagram["components"]]
    write_json(output / "02_cbb_classification.json", {"components": classifications})
    interface_map = {item["component_id"]: item for item in interfaces}
    events: list[dict[str, Any]] = []
    lock = Lock()

    def task(pair: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        index, component = pair
        with lock:
            events.append({"event": "component_started", "component_id": component["id"], "at": datetime.now(timezone.utc).isoformat()})
        result = generate_component(component, interface_map[component["id"]], index)
        write_json(output / "components" / f"{component['id']}.json", result)
        with lock:
            events.append({"event": "component_finished", "component_id": component["id"], "at": datetime.now(timezone.utc).isoformat()})
        return result

    with ThreadPoolExecutor(max_workers=min(8, len(diagram["components"]))) as pool:
        generated = list(pool.map(task, enumerate(diagram["components"])))
    schematic = {
        "schema": "tianshu-schematic/v1", "title": diagram.get("title", "Untitled schematic"),
        "components": generated,
        "wires": [{**wire, "points": []} for wire in diagram["connections"]],
        "nets": sorted({wire["net"] for wire in diagram["connections"]}),
    }
    write_json(output / "schematic.json", schematic)
    write_json(output / "events.json", events)
    manifest = {
        "status": "completed", "component_count": len(generated),
        "wire_count": len(schematic["wires"]), "public_cbb_count": sum(c["library_type"] == "public" for c in generated),
        "private_cbb_count": sum(c["library_type"] == "private" for c in generated),
        "files": ["01_signal_interfaces.json", "02_cbb_classification.json", "schematic.json", "events.json"]
                 + [f"components/{c['id']}.json" for c in generated],
    }
    write_json(output / "manifest.json", manifest)
    return {"manifest": manifest, "schematic": schematic}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run_pipeline(read_json(args.input), args.output)
    print(json.dumps(result["manifest"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
