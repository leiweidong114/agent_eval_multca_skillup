from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent_eval.env_config import effective_environment, repository_root


SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")


def external_skill_roots(project_root: Path) -> list[Path]:
    resolved_project = project_root.resolve()
    backend_root = (
        resolved_project
        if resolved_project.name.lower() == "backend"
        else resolved_project / "backend"
    )
    roots: list[Path] = [(backend_root / "extensions" / "skills").resolve()]
    raw = str(effective_environment(project_root).get("EXTERNAL_SKILL_PATHS_JSON") or "").strip()
    if not raw:
        return roots
    try:
        values = json.loads(raw)
    except ValueError as exc:
        raise ValueError("EXTERNAL_SKILL_PATHS_JSON must be a JSON array") from exc
    if not isinstance(values, list):
        raise ValueError("EXTERNAL_SKILL_PATHS_JSON must be a JSON array")
    repository = repository_root(project_root)
    for value in values:
        path = Path(str(value).strip())
        if not path.is_absolute():
            path = repository / path
        resolved = path.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def resolve_external_skill(project_root: Path, identifier: str) -> Path | None:
    if not SKILL_NAME_RE.fullmatch(identifier):
        return None
    for root in external_skill_roots(project_root):
        candidate = (root / identifier).resolve()
        if candidate.parent == root and (candidate / "SKILL.md").is_file():
            return candidate
    return None


def list_external_skills(project_root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in external_skill_roots(project_root):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if (
                child.name not in seen
                and SKILL_NAME_RE.fullmatch(child.name)
                and child.is_dir()
                and (child / "SKILL.md").is_file()
            ):
                seen.add(child.name)
                result.append({
                    "name": child.name,
                    "identifier": child.name,
                    "source": "external",
                    "path": str(child.resolve()),
                    "has_skill_md": True,
                })
    return result
