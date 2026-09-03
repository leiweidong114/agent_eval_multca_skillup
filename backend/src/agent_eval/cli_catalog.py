from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import yaml


SCHEMATIC_PIPELINE_SKILLS = (
    "schematic-pipeline",
    "signal-interface-generation",
    "schematic-layout-codegen",
    "schematic-web-apply",
)


def _skill_metadata(path: Path) -> dict[str, Any]:
    text = (path / "SKILL.md").read_text(encoding="utf-8")
    metadata: dict[str, Any] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            loaded = yaml.safe_load(parts[1]) or {}
            if isinstance(loaded, dict):
                metadata = loaded
    cases = sorted((path / "evals" / "cases").glob("*.yaml"))
    return {
        "name": str(metadata.get("name") or path.name),
        "directory_name": path.name,
        "description": str(metadata.get("description") or ""),
        "path": str(path.resolve()),
        "eval_case_count": len(cases),
        "eval_cases": [str(case.resolve()) for case in cases],
        "schematic_pipeline_member": path.name in SCHEMATIC_PIPELINE_SKILLS,
    }


def list_skills(skills_root: Path, *, pipeline_only: bool = False) -> dict[str, Any]:
    skills = []
    if skills_root.is_dir():
        for path in sorted(skills_root.iterdir()):
            if path.is_dir() and (path / "SKILL.md").is_file():
                item = _skill_metadata(path)
                if not pipeline_only or item["schematic_pipeline_member"]:
                    skills.append(item)
    return {
        "root": str(skills_root.resolve()),
        "skill_count": len(skills),
        "pipeline_only": pipeline_only,
        "skills": skills,
    }


def list_results(
    results_root: Path,
    *,
    limit: int = 20,
    agent: str | None = None,
    skill: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if results_root.is_dir():
        for report_path in results_root.rglob("evaluation-report.json"):
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            skills = report.get("skills") or [Path(str(report.get("skill") or "")).name]
            row = {
                "task_id": report.get("task_id") or report.get("run_id"),
                "created_at": report.get("created_at"),
                "status": report.get("status") or "unknown",
                "agent": report.get("agent"),
                "agent_backend": report.get("agent_backend"),
                "model": report.get("provider_model") or report.get("model"),
                "profile": report.get("model_profile"),
                "task_name": report.get("task_name"),
                "skills": skills,
                "overall_score": (report.get("scores") or {}).get("overall_score"),
                "valid_for_ranking": (report.get("scoring") or {}).get("valid_for_ranking"),
                "report": str(report_path.resolve()),
            }
            if agent and str(row["agent"]).lower() != agent.lower():
                continue
            if skill and skill not in skills:
                continue
            if status and str(row["status"]).lower() != status.lower():
                continue
            rows.append(row)
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    selected = rows[: max(1, limit)]
    return {
        "root": str(results_root.resolve()),
        "matched_count": len(rows),
        "returned_count": len(selected),
        "results": selected,
    }


def compose_skill_bundle(
    backend_root: Path,
    skill_names: tuple[str, ...] = SCHEMATIC_PIPELINE_SKILLS,
) -> Path:
    """Create a deterministic local bundle containing all selected Skills."""
    sources: list[tuple[str, Path]] = []
    digest = hashlib.sha256()
    for name in skill_names:
        source = (backend_root / "skills" / name).resolve()
        if not (source / "SKILL.md").is_file():
            raise FileNotFoundError(f"Required pipeline Skill was not found: {name}")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        for file_path in sorted(path for path in source.rglob("*") if path.is_file()):
            if "__pycache__" in file_path.parts or file_path.suffix == ".pyc":
                continue
            digest.update(file_path.relative_to(source).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_path.read_bytes())
        sources.append((name, source))
    bundle_name = f"schematic-pipeline-bundle-{digest.hexdigest()[:12]}"
    bundles_root = backend_root / ".runtime" / "composed-skills"
    destination = bundles_root / bundle_name
    if (destination / "SKILL.md").is_file():
        return destination
    temporary = bundles_root / f".{bundle_name}-{uuid.uuid4().hex}"
    temporary.mkdir(parents=True)
    lines = [
        "---",
        f"name: {bundle_name}",
        "description: Evaluate the complete four-Skill schematic generation pipeline.",
        "---",
        "",
        "# Schematic Pipeline Evaluation Bundle",
        "",
        "Read and use all four bundled Skills. The orchestration Skill is authoritative",
        "for execution order; the remaining Skills implement its three stages.",
        "",
        "## Bundled Skills",
        "",
    ]
    for index, (name, source) in enumerate(sources, start=1):
        folder = f"{index:02d}-{re.sub(r'[^A-Za-z0-9._-]+', '-', name)}"
        shutil.copytree(
            source,
            temporary / "skills" / folder,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        lines.append(f"- `{name}`: `skills/{folder}/SKILL.md`")
    (temporary / "SKILL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    bundles_root.mkdir(parents=True, exist_ok=True)
    try:
        temporary.replace(destination)
    except FileExistsError:
        shutil.rmtree(temporary, ignore_errors=True)
    return destination
