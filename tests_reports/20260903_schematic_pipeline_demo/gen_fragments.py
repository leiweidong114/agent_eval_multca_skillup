# -*- coding: utf-8 -*-
"""Dev-test fixture: emulate parallel subagent outputs for one sheet's slices.

Each worker (thread) plays the role of one subagent and writes only its own
slice file (connect code), so files can be produced concurrently without
conflicts - exactly like the batch-of-2 subagents the SKILL mandates.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _render_slice(item: dict) -> str:
    lines = [f"# slice {item['slice_id']} 的连接代码（subagent 产物）"]
    for net in item["nets"]:
        name = net["name"]
        lines.append(f"# 网络 {name}: {net.get('type', 'signal')}")
        refs = []
        for end in net["pins"]:
            refs.append(f"pin(circuit.{end['instance']}, {end['pin']})")
        lines.append(f"connect([{', '.join(refs)}], circuit.{name})")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--fragdir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    data = json.loads((args.fragdir / "slices.json").read_text(encoding="utf-8"))

    def write_one(item: dict) -> str:
        target = args.fragdir / f"{item['file_id']}.py"
        target.write_text(_render_slice(item), encoding="utf-8")
        return item["file_id"]

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        written = list(pool.map(write_one, data["slices"]))
    print("written slices:", written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
