from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

from agent_eval.model_config import describe_model_config
from agent_eval.runtime import (
    SUPPORTED_AGENTS,
    default_agent_command,
)
from app.config import BACKEND_ROOT, SKILLS_ROOT

router = APIRouter(prefix="/api", tags=["discovery"])


def _scan_skills(root: Path) -> list[dict[str, str]]:
    """Scan SKILLS_ROOT for directories containing a SKILL.md."""
    found: list[dict[str, str]] = []
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if (child / "SKILL.md").is_file():
            found.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "has_skill_md": True,
                }
            )
    return found


@router.get("/agents")
def list_agents() -> list[dict[str, str | bool | None]]:
    """List supported Multica Agent backends and local CLI discovery."""
    result: list[dict[str, str | bool | None]] = []
    for agent in SUPPORTED_AGENTS:
        command = default_agent_command(agent)
        result.append(
            {
                "agent": agent,
                "default_command": command,
                "detected_executable": shutil.which(command),
            }
        )
    return result


@router.get("/model-config")
def get_model_config() -> dict[str, object]:
    """Return non-secret model defaults used by the CLI and Web UI."""
    return describe_model_config(BACKEND_ROOT)


@router.get("/skills")
def list_skills() -> dict[str, object]:
    """List available Skills under backend/skills."""
    return {
        "root": str(SKILLS_ROOT),
        "skills": _scan_skills(SKILLS_ROOT),
    }


@router.get("/skills/{skill_name}/cases")
def list_skill_cases(skill_name: str) -> dict[str, object]:
    """List case YAML files for a specific Skill."""
    skill_dir = (SKILLS_ROOT / skill_name).resolve()
    if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_name}")
    cases_dir = skill_dir / "evals" / "cases"
    cases: list[dict[str, str]] = []
    if cases_dir.is_dir():
        for case in sorted(cases_dir.glob("*.yaml")):
            cases.append({"name": case.name, "path": str(case)})
    return {
        "skill": skill_name,
        "skill_dir": str(skill_dir),
        "cases": cases,
    }
