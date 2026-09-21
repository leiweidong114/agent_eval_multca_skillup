from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path

from agent_eval.env_config import load_root_env, repository_root


def _results_environment(project_root: Path) -> dict[str, str]:
    # A path saved from the Settings page must take effect immediately even
    # when the backend inherited an older value from its parent process.
    values = dict(os.environ)
    values.update(load_root_env(project_root))
    return values


def _resolve_results_root(project_root: Path, configured: str) -> Path:
    candidate = Path(os.path.expandvars(configured)).expanduser()
    if not candidate.is_absolute():
        candidate = repository_root(project_root) / candidate
    return candidate.resolve()


def evaluation_results_root(project_root: Path) -> Path:
    """Return the shared CLI/Web result root, honoring the root .env file.

    A short absolute location is useful on Windows: bundled Skills are copied
    below each run and can otherwise exceed the legacy MAX_PATH limit.
    """
    configured = _results_environment(project_root).get("AGENT_EVAL_RESULTS_ROOT", "").strip()
    if not configured:
        return project_root / "evaluation_results"
    return _resolve_results_root(project_root, configured)


def evaluation_results_roots(project_root: Path) -> tuple[Path, ...]:
    """Readable locations: active root, legacy default, then prior settings."""
    environment = _results_environment(project_root)
    try:
        previous = json.loads(environment.get("AGENT_EVAL_RESULTS_ROOT_HISTORY_JSON") or "[]")
    except ValueError:
        previous = []
    if not isinstance(previous, list):
        previous = []
    roots = [evaluation_results_root(project_root), project_root / "evaluation_results"]
    for value in previous[:16]:
        if isinstance(value, str) and value.strip():
            roots.append(_resolve_results_root(project_root, value.strip()))
    return tuple(dict.fromkeys(path.resolve() for path in roots))


def validate_results_root(path_text: str) -> Path:
    """Validate and probe a user-selected absolute, short, writable directory."""
    raw = path_text.strip()
    if not raw or any(char in raw for char in "\r\n\0"):
        raise ValueError("请输入有效的绝对路径")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("运行产物路径必须是绝对路径")
    resolved = candidate.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("请选择驱动器或文件系统根目录下的子文件夹")
    if os.name == "nt" and len(str(resolved)) > 80:
        raise ValueError("Windows 运行产物根路径请控制在 80 个字符以内")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".agent-eval-write-test-", dir=resolved):
            pass
    except OSError as exc:
        raise ValueError(f"运行产物目录不可写：{exc}") from exc
    return resolved
