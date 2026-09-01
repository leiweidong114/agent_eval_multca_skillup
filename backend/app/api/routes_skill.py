from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from agent_eval.database import database_health
from agent_eval.model_config import describe_model_config, resolve_config_secret
from agent_eval.runtime import (
    SUPPORTED_AGENTS,
    default_agent_command,
)
from app.config import BACKEND_ROOT, SKILLS_ROOT
from app.skill_registry import (
    delete_skill_version,
    list_uploaded_skills,
    resolve_skill,
    upload_skill,
)
from app.retention import cleanup_expired_runs, expired_runs

router = APIRouter(prefix="/api", tags=["discovery"])


class CleanupRequest(BaseModel):
    confirm: bool = False


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


@router.get("/database/health")
def get_database_health() -> dict[str, object]:
    """Check direct PostgreSQL access without exposing credentials."""
    result = database_health(BACKEND_ROOT)
    result["exact_trace_available"] = bool(
        resolve_config_secret(BACKEND_ROOT, "LITELLM_MASTER_KEY")
    )
    result["trace_note"] = (
        "Exact per-run LiteLLM correlation enabled" if result["exact_trace_available"]
        else "Set LITELLM_MASTER_KEY to enable exact correlation; current runs use model/time matching"
    )
    return result


@router.get("/privacy/retention")
def get_retention() -> dict[str, object]:
    """Preview expired local evaluation artifacts without deleting them."""
    return expired_runs()


@router.post("/privacy/retention/cleanup")
def run_retention_cleanup(request: CleanupRequest) -> dict[str, object]:
    """Explicitly delete only expired run directories under the configured run root."""
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to delete expired runs")
    return cleanup_expired_runs()


@router.get("/skills")
def list_skills() -> dict[str, object]:
    """List available Skills under backend/skills."""
    return {
        "root": str(SKILLS_ROOT),
        "skills": _scan_skills(SKILLS_ROOT) + list_uploaded_skills(),
    }


@router.post("/skills/upload")
async def upload_skill_archive(
    name: str = Form(...), archive: UploadFile = File(...)
) -> dict[str, object]:
    if not (archive.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP Skill archives are accepted")
    data = await archive.read(20 * 1024 * 1024 + 1)
    try:
        return upload_skill(name, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/skills/versions")
def list_skill_versions() -> list[dict[str, object]]:
    return list_uploaded_skills()


@router.delete("/skills/{skill_name}/versions/{version}")
def remove_skill_version(skill_name: str, version: str) -> dict[str, object]:
    try:
        if not delete_skill_version(skill_name, version):
            raise HTTPException(status_code=404, detail="Skill version not found")
        return {"deleted": True, "name": skill_name, "version": version}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/skills/{skill_name}/cases")
def list_skill_cases(skill_name: str) -> dict[str, object]:
    """List case YAML files for a specific Skill."""
    skill_dir = resolve_skill(skill_name)
    if skill_dir is None:
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
