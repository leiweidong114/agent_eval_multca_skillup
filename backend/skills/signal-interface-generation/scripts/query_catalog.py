# -*- coding: utf-8 -*-
"""Print a small, task-specific view of a fetched device catalogue.

This keeps the full catalogue out of the model context while still exposing the
exact ``library_id`` and pin numbers required to author ``sheets.json``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


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


def _matches(device: dict[str, Any], terms: list[str]) -> bool:
    haystack = " ".join(
        str(device.get(key, ""))
        for key in ("library_id", "name", "category", "subtype", "description")
    ).casefold()
    return any(term.casefold() in haystack for term in terms)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--search", action="append", default=[],
                        help="keyword; repeat for multiple component families")
    parser.add_argument("--library-id", action="append", default=[],
                        help="exact library_id; repeat for an exact, compact result")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    args.catalog = _workspace_path(args.catalog)

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    devices = payload.get("devices", payload if isinstance(payload, list) else [])
    if not args.search and not args.library_id:
        parser.error("at least one --search or --library-id is required")
    exact_ids = set(args.library_id)
    matches = [
        device for device in devices
        if device.get("library_id") in exact_ids
        or (args.search and _matches(device, args.search))
    ]
    matches = matches[: max(1, args.limit)]
    compact = []
    for device in matches:
        compact.append({
            "library_id": device.get("library_id"),
            "name": device.get("name"),
            "category": device.get("category"),
            "subtype": device.get("subtype"),
            "description": device.get("description"),
            "pins": [
                {"number": str(pin.get("number")), "name": pin.get("name")}
                for pin in device.get("pins", [])
            ],
        })
    print(json.dumps({"count": len(compact), "devices": compact},
                     ensure_ascii=False, indent=2))
    return 0 if compact else 1


if __name__ == "__main__":
    raise SystemExit(main())
