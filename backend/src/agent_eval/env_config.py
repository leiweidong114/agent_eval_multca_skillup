from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Mapping


ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_APPLIED_ROOT_VALUES: dict[str, str] = {}


def repository_root(project_root: Path) -> Path:
    resolved = project_root.resolve()
    return resolved.parent if resolved.name.lower() == "backend" else resolved


def env_file_path(project_root: Path) -> Path:
    return repository_root(project_root) / ".env"


def _decode_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, str) else str(decoded)
        except json.JSONDecodeError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def load_root_env(project_root: Path) -> dict[str, str]:
    """Read the single deployment configuration file at repository-root .env."""
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
        if not ENV_NAME_RE.fullmatch(key):
            raise ValueError(f"Invalid .env variable name at {path}:{line_number}: {key!r}")
        result[key] = _decode_value(value)
    return result


def effective_environment(
    project_root: Path, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    # The repository .env is the deployment source of truth. System variables
    # remain available for OS facilities (PATH, APPDATA, TEMP), but a same-name
    # application setting in .env wins.
    result = {
        key: value
        for key, value in os.environ.items()
        if _APPLIED_ROOT_VALUES.get(key) != value
    }
    result.update(load_root_env(project_root))
    if environ is not None:
        result.update(dict(environ))
    return result


def apply_root_env(project_root: Path, *, override: bool = True) -> dict[str, str]:
    loaded = load_root_env(project_root)
    for key, value in loaded.items():
        if override or key not in os.environ:
            os.environ[key] = value
            _APPLIED_ROOT_VALUES[key] = value
    return loaded


def _encoded_value(value: str) -> str:
    if not value:
        return ""
    if re.fullmatch(r"[^\s#'\"\r\n]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def update_root_env(
    project_root: Path, updates: Mapping[str, str | None]
) -> dict[str, str]:
    """Atomically update selected values while preserving comments and unrelated keys."""
    invalid = [key for key in updates if not ENV_NAME_RE.fullmatch(key)]
    if invalid:
        raise ValueError(f"Invalid .env variable name: {invalid[0]!r}")
    path = env_file_path(project_root)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if not match or match.group(1) not in remaining:
            output.append(line)
            continue
        key = match.group(1)
        value = remaining.pop(key)
        if value is not None:
            output.append(f"{key}={_encoded_value(str(value))}")
    for key, value in remaining.items():
        if value is not None:
            output.append(f"{key}={_encoded_value(str(value))}")
    content = "\n".join(output).rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="..env.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return load_root_env(project_root)
