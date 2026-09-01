---
name: schematic-generation
description: Convert a web block diagram into signal interfaces, classify public/private CBBs, generate per-device circuits in parallel, package a Tianshu-style schematic JSON, and return a project URL. Use for complete schematic-generation tasks and artifact evaluation.
---

# Schematic Generation

Run `scripts/schematic_pipeline.py` with a block-diagram JSON input and output directory.

```bash
python scripts/schematic_pipeline.py --input diagram.json --output generated
```

The workflow must preserve component IDs, pin IDs, directions and net names:

1. Validate the block diagram.
2. Write `01_signal_interfaces.json`, one interface block per component.
3. Write `02_cbb_classification.json` using each component's `library_type`.
4. Generate `components/<id>.json` concurrently. Public CBBs use reference templates; private CBBs synthesize a component circuit from the interface list.
5. Package all components and connections into `schematic.json` and write `manifest.json` plus lifecycle `events.json`.

Run the specialized judge after generation:

```bash
python scripts/schematic_judge.py --input diagram.json --output generated
```

See `references/format.md` for the artifact contract. Never invent missing pin references or silently drop a connection.
