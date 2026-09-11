from __future__ import annotations

import shutil
import subprocess
import json
import sys
import time
import base64
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
import httpx
from pydantic import BaseModel, Field, SecretStr

from agent_eval.database import database_health
from agent_eval.failure import describe_evaluation_failure
from agent_eval.agent_contract import describe_agent_contract
from agent_eval.model_config import (
    describe_model_config,
    delete_model_profile,
    discover_available_models,
    gateway_request_headers,
    load_litellm_model_catalog,
    load_runtime_settings,
    list_model_profiles,
    resolve_config_secret,
    resolve_model_profile,
    refresh_litellm_model_catalog,
    save_runtime_settings,
    save_model_profile,
)
from agent_eval.runtime import (
    SUPPORTED_AGENTS,
    agent_capabilities,
    default_agent_command,
    load_agent_paths,
    save_agent_path,
)
from agent_eval.scoring import load_scoring_config
from agent_eval.cli_catalog import SCHEMATIC_PIPELINE_SKILLS
from agent_eval.skill_sources import list_external_skills
from agent_eval.evaluators import resolve_evaluator
from agent_eval.schematic_tasks import normalize_schematic_task_profiles
from app.config import BACKEND_ROOT, SKILLS_ROOT
from app.auth import employee_from_request
from app.skill_registry import (
    delete_skill,
    delete_skill_version,
    list_uploaded_skills,
    resolve_skill,
    upload_skill,
)
from app.retention import cleanup_expired_runs, expired_runs

router = APIRouter(prefix="/api", tags=["discovery"])


class CleanupRequest(BaseModel):
    confirm: bool = False


class DeleteSkillRequest(BaseModel):
    confirm: bool = False


class ModelTestRequest(BaseModel):
    model: str = Field(min_length=1, max_length=300)
    profile: str = Field(default="litellm", min_length=1, max_length=200)


class BatchModelTestRequest(BaseModel):
    workers: int = Field(default=8, ge=1, le=16)
    timeout_seconds: float = Field(default=30, ge=3, le=180)


class AgentPathRequest(BaseModel):
    path: str = Field(default="", max_length=4096)


class SchematicTaskProfileRequest(BaseModel):
    skills: list[str] = Field(min_length=1, max_length=8)
    evaluator_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")


class RuntimeSettingsRequest(BaseModel):
    judge_model: str = Field(min_length=1, max_length=300)
    agent_test_model: str = Field(min_length=1, max_length=300)
    schematic_skills: list[str] = Field(
        default_factory=lambda: list(SCHEMATIC_PIPELINE_SKILLS), min_length=1, max_length=8
    )
    schematic_task_profiles: dict[str, SchematicTaskProfileRequest] = Field(default_factory=dict)


class ModelProfileRequest(BaseModel):
    model: str = Field(min_length=1, max_length=300)
    api_base: str = Field(min_length=8, max_length=1000)
    api_key_env: str = Field(default="LITELLM_API_KEY", min_length=1, max_length=100)
    api_key: SecretStr | None = None
    protocol: str = "openai_compatible"
    context_window: int = Field(default=200000, gt=0, le=10_000_000)
    max_output_tokens: int = Field(default=32000, gt=0, le=1_000_000)
    agent_models: dict[str, str] = Field(default_factory=dict)
    gateway_models: dict[str, str] = Field(default_factory=dict)
    make_default: bool = False


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
                    "identifier": child.name,
                    "source": "built_in",
                    "path": str(child),
                    "has_skill_md": True,
                }
            )
    return found


@router.get("/agents")
def list_agents() -> list[dict[str, Any]]:
    """List supported Multica Agent backends and local CLI discovery."""
    result: list[dict[str, str | bool | None]] = []
    configured_paths = load_agent_paths(BACKEND_ROOT)
    for agent in SUPPORTED_AGENTS:
        command = default_agent_command(agent, BACKEND_ROOT)
        result.append(
            {
                "agent": agent,
                "default_command": command,
                "detected_executable": shutil.which(command),
                "configured_path": configured_paths.get(agent),
                "capabilities": agent_capabilities(agent),
                "evaluation_contract": describe_agent_contract(agent),
            }
        )
    return result


@router.put("/agents/{agent_name}/path")
def put_agent_path(agent_name: str, request: AgentPathRequest) -> dict[str, object]:
    """Persist an Agent executable override used by both Web and CLI runs."""
    if agent_name not in SUPPORTED_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unsupported Agent: {agent_name}")
    try:
        configured = save_agent_path(
            agent_name,
            request.path,
            project_root=BACKEND_ROOT,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    command = default_agent_command(agent_name, BACKEND_ROOT)
    return {
        "agent": agent_name,
        "configured_path": configured.get(agent_name),
        "default_command": command,
        "detected_executable": shutil.which(command),
    }


@router.post("/agents/{agent_name}/test")
def test_agent(agent_name: str) -> dict[str, object]:
    """Run the Agent with a minimal prompt through the configured default model."""
    if agent_name not in SUPPORTED_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unsupported Agent: {agent_name}")
    command = default_agent_command(agent_name, BACKEND_ROOT)
    executable = shutil.which(command)
    if not executable:
        return {"ok": False, "agent": agent_name, "message": "未在 PATH 中发现可执行文件"}
    settings = load_runtime_settings(BACKEND_ROOT)
    model_config = describe_model_config(BACKEND_ROOT)
    model = settings.get("agent_test_model") or str(model_config.get("default_model") or "")
    if not model:
        return {"ok": False, "agent": agent_name, "executable": executable, "message": "尚未配置 Agent 测试模型"}
    started = time.perf_counter()
    try:
        process = subprocess.run(
            [
                sys.executable, "-m", "agent_eval.cli", "check-agent",
                "--agent", agent_name, "--model", model,
                "--prompt", "HI", "--timeout", "120",
            ],
            capture_output=True,
            text=True,
            timeout=150,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        raw = (process.stdout or "").strip()
        try:
            result = json.loads(raw)
        except ValueError:
            result = {}
        ok = process.returncode == 0 and result.get("status") == "connected"
        return {
            "ok": ok,
            "agent": agent_name,
            "executable": executable,
            "model": model,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "message": (
                "Agent 已通过模型完成 HI 请求"
                if ok else str(result.get("error") or (result.get("failure") or {}).get("detail") or process.stderr or f"进程退出码 {process.returncode}")[:500]
            ),
            "result": result,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "agent": agent_name,
            "executable": executable,
            "model": model,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "message": "检测超时" if isinstance(exc, subprocess.TimeoutExpired) else str(exc),
        }


@router.get("/model-config")
def get_model_config() -> dict[str, object]:
    """Return non-secret model defaults used by the CLI and Web UI."""
    result = describe_model_config(BACKEND_ROOT)
    judge = (load_scoring_config(BACKEND_ROOT).get("llm_judge") or {}).copy()
    settings = load_runtime_settings(BACKEND_ROOT)
    judge["model"] = settings.get("judge_model") or resolve_config_secret(BACKEND_ROOT, "LITELLM_JUDGE_MODEL") or judge.get("model")
    result["llm_judge"] = {
        key: judge.get(key)
        for key in (
            "enabled", "required", "model", "timeout_seconds",
            "max_evidence_chars", "temperature",
        )
    }
    return result


@router.get("/model-profiles")
def get_model_profiles() -> list[dict[str, Any]]:
    """List provider profiles without returning API-key values."""
    return list_model_profiles(BACKEND_ROOT)


@router.put("/model-profiles/{profile_name}")
def put_model_profile(
    profile_name: str, request: ModelProfileRequest
) -> dict[str, Any]:
    """Create or update an ignored local CC-Switch-style provider profile."""
    try:
        values = request.model_dump(exclude={"api_key", "make_default"})
        return save_model_profile(
            BACKEND_ROOT,
            profile_name,
            values,
            api_key=(request.api_key.get_secret_value() if request.api_key else None),
            make_default=request.make_default,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/model-profiles/{profile_name}")
def remove_model_profile(profile_name: str) -> dict[str, object]:
    """Remove a local profile or restore a built-in profile overridden locally."""
    try:
        removed = delete_model_profile(BACKEND_ROOT, profile_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Local model profile not found")
    return {"removed": True, "profile": profile_name}


@router.get("/models")
def list_models(request: Request) -> dict[str, object]:
    """Return models discovered from LiteLLM plus configured native fallbacks."""
    result = discover_available_models(
        BACKEND_ROOT, employee_no=employee_from_request(request)
    )
    try:
        catalog = load_litellm_model_catalog(BACKEND_ROOT)
    except (OSError, ValueError):
        catalog = {}
    tested = {
        str(item.get("id")): item
        for item in [*(catalog.get("models") or []), *(catalog.get("unavailable_models") or [])]
        if isinstance(item, dict) and item.get("id")
    }
    for item in result.get("models") or []:
        probe = tested.get(str(item.get("id")))
        item["connectivity"] = (
            {**probe, "tested_at": catalog.get("refreshed_at")} if probe else {"available": None, "tested_at": None}
        )
    result["connectivity_summary"] = {
        "tested_at": catalog.get("refreshed_at"),
        "available": len(catalog.get("models") or []),
        "unavailable": len(catalog.get("unavailable_models") or []),
    }
    return result


@router.post("/models/test-batch")
def test_models_batch(payload: BatchModelTestRequest, request: Request) -> dict[str, object]:
    """Probe every LiteLLM-visible model with a real HI inference request."""
    return refresh_litellm_model_catalog(
        BACKEND_ROOT,
        employee_no=employee_from_request(request),
        probe_timeout=payload.timeout_seconds,
        probe_workers=payload.workers,
    )


@router.get("/settings")
def get_runtime_settings() -> dict[str, object]:
    configured = load_runtime_settings(BACKEND_ROOT)
    model_config = describe_model_config(BACKEND_ROOT)
    scoring = load_scoring_config(BACKEND_ROOT).get("llm_judge") or {}
    default_model = str(model_config.get("default_model") or "")
    return {
        "judge_model": (
            configured.get("judge_model")
            or resolve_config_secret(BACKEND_ROOT, "LITELLM_JUDGE_MODEL")
            or str(scoring.get("model") or default_model)
        ),
        "agent_test_model": configured.get("agent_test_model") or default_model,
        "schematic_skills": configured.get("schematic_skills") or list(SCHEMATIC_PIPELINE_SKILLS),
        "schematic_task_profiles": configured.get("schematic_task_profiles") or {},
    }


@router.put("/settings")
def put_runtime_settings(request: RuntimeSettingsRequest) -> dict[str, object]:
    try:
        payload = request.model_dump()
        profiles = normalize_schematic_task_profiles(
            payload.get("schematic_task_profiles"),
            legacy_skills=request.schematic_skills,
        )
        for task_type, profile in profiles.items():
            missing = [name for name in profile["skills"] if resolve_skill(name) is None]
            if missing:
                raise ValueError(f"Skill not found for {task_type}: {', '.join(missing)}")
            resolve_evaluator(
                BACKEND_ROOT,
                evaluation_type="schematic",
                evaluator_id=profile["evaluator_id"],
                schematic_task_type=task_type,
            )
        payload["schematic_task_profiles"] = profiles
        return save_runtime_settings(BACKEND_ROOT, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/models/test")
def test_model(payload: ModelTestRequest, request: Request) -> dict[str, object]:
    """Send a minimal non-streaming completion through the selected LiteLLM profile."""
    started = time.perf_counter()
    try:
        profile = resolve_model_profile(
            BACKEND_ROOT,
            profile_name=payload.profile,
            model_override=payload.model,
        )
        if not profile.api_base:
            return {
                "ok": True,
                "model": payload.model,
                "profile": payload.profile,
                "duration_ms": 0,
                "message": "本地原生模型配置有效；实际可用性由 Agent 负责",
            }
        key = profile.environment["LITELLM_API_KEY"]
        if profile.protocol == "anthropic_messages":
            endpoint = f"{profile.environment['ANTHROPIC_BASE_URL'].rstrip('/')}/v1/messages"
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            request_body = {
                "model": payload.model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 4,
            }
        elif profile.protocol == "openai_responses":
            endpoint = f"{profile.environment['OPENAI_BASE_URL'].rstrip('/')}/responses"
            headers = {"Authorization": f"Bearer {key}"}
            request_body = {"model": payload.model, "input": "Reply with OK.", "max_output_tokens": 4}
        else:
            endpoint = f"{profile.environment['OPENAI_BASE_URL'].rstrip('/')}/chat/completions"
            headers = {"Authorization": f"Bearer {key}"}
            request_body = {
                "model": payload.model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 4,
                "temperature": 0,
                "stream": False,
            }
        headers.update(gateway_request_headers(
            BACKEND_ROOT, profile, employee_from_request(request)
        ))
        response = httpx.post(
            endpoint,
            headers=headers,
            json=request_body,
            timeout=30.0,
            trust_env=False,
        )
        response.raise_for_status()
        response_payload = response.json()
        actual_model = (
            str(response_payload.get("model") or payload.model)
            if isinstance(response_payload, dict)
            else payload.model
        )
        return {
            "ok": True,
            "model": payload.model,
            "actual_model": actual_model,
            "profile": payload.profile,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "message": "模型响应正常",
        }
    except (ValueError, httpx.HTTPError) as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            failure = describe_evaluation_failure(
                exc.response.text, status_code=exc.response.status_code,
                component="model_probe",
            )
        else:
            failure = describe_evaluation_failure(
                str(exc), component="model_probe"
            )
        return {
            "ok": False,
            "model": payload.model,
            "profile": payload.profile,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "message": (failure or {}).get("detail") or str(exc)[:500],
            "failure": failure,
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
    """List built-in, uploaded, and .env-configured external Skills."""
    return {
        "root": str(SKILLS_ROOT),
        "skills": (
            _scan_skills(SKILLS_ROOT)
            + list_uploaded_skills()
            + list_external_skills(BACKEND_ROOT)
        ),
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


@router.delete("/skills/{skill_identifier}")
def remove_skill(skill_identifier: str, request: DeleteSkillRequest) -> dict[str, object]:
    """Permanently remove one exact Skill entry after explicit confirmation."""
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to permanently delete this Skill")
    try:
        result = delete_skill(skill_identifier)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"deleted": True, **result}


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
