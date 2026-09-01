from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Mapping

import yaml

from agent_eval.runtime import default_agent_command, normalize_agent


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Agent configuration must be a mapping: {path}")
    return value


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_agent_config(project_root: Path) -> dict[str, Any]:
    config_dir = project_root / "config"
    return _merge(
        _read_yaml(config_dir / "agents.yaml"),
        _read_yaml(config_dir / "local.yaml"),
    )


def _expanded(value: str, environ: Mapping[str, str]) -> str:
    result = value
    for name, item in environ.items():
        result = result.replace(f"${{{name}}}", item).replace(f"%{name}%", item)
    return os.path.expanduser(result)


def resolve_agent_executable(
    project_root: Path,
    agent: str,
    explicit: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    agent = normalize_agent(agent)
    source_environment = environ if environ is not None else os.environ
    config = load_agent_config(project_root)
    entries = config.get("agents") or {}
    entry = entries.get(agent) or {} if isinstance(entries, dict) else {}
    configured = entry.get("executable") if isinstance(entry, dict) else None
    value = str(explicit or configured or default_agent_command(agent)).strip()
    if not value:
        raise ValueError(f"Agent executable is empty: {agent}")
    return _expanded(value, source_environment)


def describe_agents(project_root: Path) -> list[dict[str, str | bool | None]]:
    from agent_eval.runtime import SUPPORTED_AGENTS

    result: list[dict[str, str | bool | None]] = []
    for agent in SUPPORTED_AGENTS:
        configured = resolve_agent_executable(project_root, agent)
        path = Path(configured)
        detected = str(path.resolve()) if path.is_file() else shutil.which(configured)
        result.append(
            {
                "agent": agent,
                "default_command": default_agent_command(agent),
                "configured_executable": configured,
                "detected_executable": detected,
                "available": bool(detected),
            }
        )
    return result
