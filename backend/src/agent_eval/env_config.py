from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping


def repository_root(project_root: Path) -> Path:
    """Return the repository root for a backend project path."""
    resolved = project_root.resolve()
    return resolved.parent if resolved.name.lower() == "backend" else resolved


def env_file_path(project_root: Path) -> Path:
    return repository_root(project_root) / ".env"


def load_root_env(project_root: Path) -> dict[str, str]:
    """Read the repository-root .env without changing process globals."""
    path = env_file_path(project_root)
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid .env entry at {path}:{line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid .env variable name at {path}:{line_number}: {key!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key] = value
    return result


def effective_environment(
    project_root: Path, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Merge root .env with an explicit/process environment.

    Real process variables win over .env, which makes temporary CI and command-line
    overrides possible without editing the deployment file.
    """
    result = load_root_env(project_root)
    result.update(dict(os.environ if environ is None else environ))
    return result


def apply_root_env(project_root: Path, *, override: bool = False) -> dict[str, str]:
    """Load root .env into the process for components that read os.environ directly."""
    loaded = load_root_env(project_root)
    for key, value in loaded.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded
