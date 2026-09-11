from __future__ import annotations

import importlib.util
import hashlib
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from agent_eval.env_config import effective_environment, repository_root
from agent_eval.evaluators.default import GENERIC_EVALUATOR
from agent_eval.evaluators.protocol import EVALUATOR_API_VERSION, EvaluationPlugin
from agent_eval.schematic_tasks import SCHEMATIC_TASK_TYPES


ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _backend_root(project_root: Path) -> Path:
    resolved_project = project_root.resolve()
    return (
        resolved_project
        if resolved_project.name.lower() == "backend"
        else resolved_project / "backend"
    )


def _external_roots(project_root: Path) -> list[Path]:
    environment = effective_environment(project_root)
    backend_root = _backend_root(project_root)
    result: list[Path] = [(backend_root / "extensions" / "evaluators").resolve()]
    raw = str(environment.get("EVALUATOR_PLUGIN_PATHS_JSON") or "").strip()
    if not raw:
        return result
    try:
        values = json.loads(raw)
    except ValueError as exc:
        raise ValueError("EVALUATOR_PLUGIN_PATHS_JSON must be a JSON array") from exc
    if not isinstance(values, list):
        raise ValueError("EVALUATOR_PLUGIN_PATHS_JSON must be a JSON array")
    root = repository_root(project_root)
    for value in values:
        path = Path(str(value).strip())
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve()
        if resolved not in result:
            result.append(resolved)
    return result


def _load_module(path: Path) -> ModuleType:
    suffix = hashlib.sha256(str(path.parent.resolve()).encode("utf-8")).hexdigest()[:16]
    package_name = f"agent_eval_external_{suffix}"
    package = ModuleType(package_name)
    package.__path__ = [str(path.parent)]  # type: ignore[attr-defined]
    package.__package__ = package_name
    sys.modules[package_name] = package
    module_name = f"{package_name}.evaluator"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load evaluator module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _validate(plugin: Any, *, source: str) -> EvaluationPlugin:
    required = ("id", "version", "api_version", "evaluation_types", "evaluate")
    missing = [name for name in required if not hasattr(plugin, name)]
    if missing:
        raise ValueError(f"Evaluator {source} is missing {missing[0]}")
    if not ID_RE.fullmatch(str(plugin.id)):
        raise ValueError(f"Evaluator {source} has an invalid id")
    if plugin.api_version != EVALUATOR_API_VERSION:
        raise ValueError(
            f"Evaluator {plugin.id} requires {plugin.api_version}; expected {EVALUATOR_API_VERSION}"
        )
    evaluation_types = tuple(plugin.evaluation_types)
    if not evaluation_types or any(value not in {"skill", "schematic"} for value in evaluation_types):
        raise ValueError(f"Evaluator {plugin.id} has invalid evaluation_types")
    task_types = tuple(getattr(plugin, "schematic_task_types", ()))
    if task_types and any(value not in SCHEMATIC_TASK_TYPES for value in task_types):
        raise ValueError(f"Evaluator {plugin.id} has invalid schematic_task_types")
    if not callable(plugin.evaluate):
        raise ValueError(f"Evaluator {plugin.id} has no callable evaluate method")
    return plugin


def _plugins(project_root: Path) -> dict[str, tuple[EvaluationPlugin, str]]:
    plugins: dict[str, tuple[EvaluationPlugin, str]] = {
        GENERIC_EVALUATOR.id: (GENERIC_EVALUATOR, "built_in"),
    }
    roots = [((_backend_root(project_root) / "evaluator_plugins").resolve(), "bundled")]
    roots.extend((root, "external") for root in _external_roots(project_root))
    for root, source_kind in roots:
        candidates = [root / "evaluator.py"] if (root / "evaluator.py").is_file() else []
        if root.is_dir():
            candidates.extend(sorted(root.glob("*/evaluator.py")))
        for path in candidates:
            module = _load_module(path)
            if not hasattr(module, "PLUGIN"):
                raise ValueError(f"External evaluator must export PLUGIN: {path}")
            plugin = _validate(module.PLUGIN, source=str(path))
            if plugin.id in plugins:
                raise ValueError(f"Duplicate evaluator id: {plugin.id}")
            source = source_kind if source_kind == "bundled" else str(path.parent.resolve())
            plugins[plugin.id] = (plugin, source)
    return plugins


def list_evaluators(project_root: Path) -> list[dict[str, Any]]:
    result = []
    for plugin, source in _plugins(project_root).values():
        result.append({
            "id": plugin.id,
            "version": str(plugin.version),
            "api_version": plugin.api_version,
            "evaluation_types": list(plugin.evaluation_types),
            "schematic_task_types": list(getattr(plugin, "schematic_task_types", ())),
            "source": source,
        })
    return sorted(result, key=lambda item: item["id"])


def resolve_evaluator(
    project_root: Path,
    *,
    evaluation_type: str,
    evaluator_id: str | None = None,
    schematic_task_type: str | None = None,
) -> EvaluationPlugin:
    if evaluation_type not in {"skill", "schematic"}:
        raise ValueError(f"Unsupported evaluation_type: {evaluation_type}")
    environment = effective_environment(project_root)
    selected = evaluator_id or str(environment.get(
        "DEFAULT_SCHEMATIC_EVALUATOR" if evaluation_type == "schematic" else "DEFAULT_SKILL_EVALUATOR"
    ) or ("schematic-default" if evaluation_type == "schematic" else "generic")).strip()
    plugins = _plugins(project_root)
    if selected not in plugins:
        raise ValueError(f"Evaluator is not installed: {selected}")
    plugin = plugins[selected][0]
    if evaluation_type not in plugin.evaluation_types:
        raise ValueError(f"Evaluator {selected} does not support {evaluation_type}")
    supported_tasks = tuple(getattr(plugin, "schematic_task_types", ()))
    if schematic_task_type and supported_tasks and schematic_task_type not in supported_tasks:
        raise ValueError(
            f"Evaluator {selected} does not support schematic task {schematic_task_type}"
        )
    return plugin
