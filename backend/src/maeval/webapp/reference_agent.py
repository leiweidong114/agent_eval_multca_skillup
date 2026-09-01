from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from maeval.adapters import get_adapter
from maeval.models import AdapterResult, Candidate, ScorerSpec, Task


PROTOCOL_VERSION = "reference-patch-agent-v1"
DEFAULT_ALLOWED_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
IGNORED_PARTS = {
    ".git",
    ".maeval",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
}


def _repository_context(workdir: Path, max_chars: int) -> tuple[str, list[str]]:
    chunks: list[str] = []
    included: list[str] = []
    used = 0
    for path in sorted(workdir.rglob("*")):
        relative = path.relative_to(workdir)
        if not path.is_file() or any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() not in DEFAULT_ALLOWED_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        header = f"\n--- FILE: {relative.as_posix()} ---\n"
        remaining = max_chars - used - len(header)
        if remaining <= 0:
            break
        if len(content) > remaining:
            continue
        chunks.append(header + content)
        included.append(relative.as_posix())
        used += len(header) + len(content)
        if used >= max_chars:
            break
    return "".join(chunks), included


def _extract_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("model response did not contain a JSON object")


def _apply_files(
    workdir: Path,
    payload: dict[str, Any],
    max_files: int,
    allowed_paths: set[str] | None = None,
) -> list[str]:
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("response JSON must contain a non-empty 'files' object")
    if len(files) > max_files:
        raise ValueError(f"response changes {len(files)} files; budget allows {max_files}")
    root = workdir.resolve()
    changed: list[str] = []
    for relative_name, content in files.items():
        if not isinstance(relative_name, str) or not isinstance(content, str):
            raise ValueError("file names and contents must be strings")
        relative = Path(relative_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe file path: {relative_name}")
        normalized = relative.as_posix()
        if allowed_paths is not None and normalized not in allowed_paths:
            raise ValueError(f"file was not included in the frozen snapshot: {relative_name}")
        target = (root / relative).resolve()
        if root not in target.parents or target.suffix.lower() not in DEFAULT_ALLOWED_SUFFIXES:
            raise ValueError(f"file path is outside the allowed workspace: {relative_name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        changed.append(normalized)
    return changed


def run_reference_agent(
    candidate: Candidate,
    task_prompt: str,
    workdir: Path,
    *,
    max_context_chars: int = 60_000,
    max_files_changed: int = 8,
) -> tuple[AdapterResult, list[str]]:
    context, included_files = _repository_context(workdir, max_context_chars)
    prompt = f"""You are the model inside a controlled repository patch executor.
You have no tools and receive exactly one repository snapshot. Solve the task by returning JSON only.

Required schema:
{{"files": {{"relative/path.py": "complete replacement file content"}}, "summary": "short note"}}

Rules:
- Include complete replacement content for every file you change.
- Use only relative paths visible in the repository snapshot.
- Do not use markdown fences or prose outside the JSON object.
- Change at most {max_files_changed} files.

Task:
{task_prompt}

Repository snapshot ({len(included_files)} files, protocol {PROTOCOL_VERSION}):
{context}
"""
    model_task = Task(
        id="reference-agent-patch",
        kind="direct",
        prompt=prompt,
        scorer=ScorerSpec(type="exact", expected=""),
        max_tokens=candidate.max_tokens,
    )
    result = get_adapter(candidate.adapter).run(candidate, model_task, None)
    if not result.ok:
        return result, []
    try:
        payload = _extract_object(result.text)
        changed = _apply_files(
            workdir, payload, max_files_changed, allowed_paths=set(included_files)
        )
    except (OSError, ValueError) as exc:
        return AdapterResult(
            ok=False,
            text=result.text,
            raw=result.raw,
            error=f"reference agent protocol error: {exc}",
            duration_api_ms=result.duration_api_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cost_usd=result.cost_usd,
            actual_model=result.actual_model,
        ), []
    result.raw = {
        "model_response": result.raw,
        "reference_agent": {
            "protocol_version": PROTOCOL_VERSION,
            "included_files": included_files,
            "changed_files": changed,
        },
    }
    return result, changed
