from __future__ import annotations

from pathlib import Path
from typing import Any


def build_artifact_manifest(root: Path) -> tuple[dict[str, Any], ...]:
    """Describe files collected by Skill-Up using paths scoped to its output directory."""
    if not root.is_dir():
        return ()
    resolved_root = root.resolve()
    artifacts: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        try:
            path.resolve().relative_to(resolved_root)
            size = path.stat().st_size
        except (OSError, ValueError):
            continue
        artifacts.append({
            "path": path.relative_to(root).as_posix(),
            "size_bytes": size,
            "suffix": path.suffix.lower(),
        })
    return tuple(artifacts)
