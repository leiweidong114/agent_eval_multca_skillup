from __future__ import annotations

import shutil
import subprocess
import time
import base64
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
import httpx
from pydantic import BaseModel, Field

from agent_eval.database import database_health
from agent_eval.agent_contract import describe_agent_contract
from agent_eval.model_config import (
    describe_model_config,
    discover_available_models,
    resolve_config_secret,
    resolve_model_profile,
)
from agent_eval.runtime import (
    SUPPORTED_AGENTS,
    agent_capabilities,
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


class ModelTestRequest(BaseModel):
    model: str = Field(min_length=1, max_length=300)
    profile: str = Field(min_length=1, max_length=200)


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
def list_agents() -> list[dict[str, Any]]:
    """List supported Multica Agent backends and local CLI discovery."""
    result: list[dict[str, str | bool | None]] = []
    for agent in SUPPORTED_AGENTS:
        command = default_agent_command(agent)
        result.append(
            {
                "agent": agent,
                "default_command": command,
                "detected_executable": shutil.which(command),
                "capabilities": agent_capabilities(agent),
                "evaluation_contract": describe_agent_contract(agent),
            }
        )
    return result


@router.post("/agents/{agent_name}/test")
def test_agent(agent_name: str) -> dict[str, object]:
    """Run a harmless version probe against a discovered local Agent CLI."""
    if agent_name not in SUPPORTED_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unsupported Agent: {agent_name}")
    command = default_agent_command(agent_name)
    executable = shutil.which(command)
    if not executable:
        return {"ok": False, "agent": agent_name, "message": "未在 PATH 中发现可执行文件"}
    started = time.perf_counter()
    try:
        process = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        output = (process.stdout or process.stderr or "").strip().splitlines()
        return {
            "ok": process.returncode == 0,
            "agent": agent_name,
            "executable": executable,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "message": output[0][:300] if output else f"进程退出码 {process.returncode}",
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "agent": agent_name,
            "executable": executable,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "message": "检测超时" if isinstance(exc, subprocess.TimeoutExpired) else str(exc),
        }


@router.get("/model-config")
def get_model_config() -> dict[str, object]:
    """Return non-secret model defaults used by the CLI and Web UI."""
    return describe_model_config(BACKEND_ROOT)


@router.get("/models")
def list_models() -> dict[str, object]:
    """Return models discovered from LiteLLM plus configured native fallbacks."""
    result = discover_available_models(BACKEND_ROOT)
    excluded = {"deepseek", "deepseek-v4-flash", "deepseek-v4-pro"}
    result["models"] = [item for item in result.get("models", []) if item.get("id") not in excluded]
    return result


@router.post("/models/test")
def test_model(request: ModelTestRequest) -> dict[str, object]:
    """Send a minimal non-streaming completion through the selected LiteLLM profile."""
    started = time.perf_counter()
    try:
        profile = resolve_model_profile(
            BACKEND_ROOT,
            profile_name=request.profile,
            model_override=request.model,
        )
        if not profile.api_base:
            return {
                "ok": True,
                "model": request.model,
                "profile": request.profile,
                "duration_ms": 0,
                "message": "本地原生模型配置有效；实际可用性由 Agent 负责",
            }
        openai_base = profile.environment["OPENAI_BASE_URL"].rstrip("/")
        response = httpx.post(
            f"{openai_base}/chat/completions",
            headers={"Authorization": f"Bearer {profile.environment['LITELLM_API_KEY']}"},
            json={
                "model": request.model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 4,
                "temperature": 0,
                "stream": False,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        actual_model = str(payload.get("model") or request.model) if isinstance(payload, dict) else request.model
        return {
            "ok": True,
            "model": request.model,
            "actual_model": actual_model,
            "profile": request.profile,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "message": "模型响应正常",
        }
    except (ValueError, httpx.HTTPError) as exc:
        return {
            "ok": False,
            "model": request.model,
            "profile": request.profile,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "message": str(exc)[:500],
        }


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


@router.get("/skills/{skill_name}")
def get_skill(skill_name: str) -> dict[str, object]:
    skill_dir = resolve_skill(skill_name)
    if skill_dir is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_name}")
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    cases_dir = skill_dir / "evals" / "cases"
    return {
        "name": skill_name,
        "path": str(skill_dir),
        "content": content,
        "case_count": len(list(cases_dir.glob("*.yaml"))) if cases_dir.is_dir() else 0,
        "files": sorted(
            str(path.relative_to(skill_dir)).replace("\\", "/")
            for path in skill_dir.rglob("*")
            if path.is_file()
        )[:500],
    }


@router.get("/skills/{skill_name}/files/{file_path:path}")
def get_skill_file(skill_name: str, file_path: str) -> dict[str, object]:
    """Return one safely resolved Skill file for the built-in file reader."""
    skill_dir = resolve_skill(skill_name)
    if skill_dir is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_name}")
    root = skill_dir.resolve()
    target = (root / file_path).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="File path escapes the Skill directory")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Skill file not found")
    if target.stat().st_size > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File is larger than the 2 MB preview limit")
    data = target.read_bytes()
    mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    try:
        content = data.decode("utf-8")
        return {"path": file_path, "kind": "text", "mime_type": mime_type, "content": content}
    except UnicodeDecodeError:
        if mime_type.startswith("image/"):
            return {
                "path": file_path,
                "kind": "image",
                "mime_type": mime_type,
                "content": base64.b64encode(data).decode("ascii"),
            }
        return {"path": file_path, "kind": "binary", "mime_type": mime_type, "size": len(data)}
