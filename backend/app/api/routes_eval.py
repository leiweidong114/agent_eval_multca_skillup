from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent_eval.runner import run_evaluation
from app.config import BACKEND_ROOT, RUNS_ROOT
from app.job_manager import job_manager
from app.skill_registry import resolve_skill

router = APIRouter(prefix="/api", tags=["eval"])


class RunRequest(BaseModel):
    user_id: str = Field(default="local-user", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
    client_task_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
    skill: str = Field(..., description="Skill name under backend/skills")
    agent: str = Field(..., description="Multica Agent backend name")
    model: str | None = Field(default=None, description="Optional profile model override")
    profile: str | None = Field(default=None, description="Profile from config/models.yaml")
    case: list[str] = Field(default_factory=list, description="Case YAML file paths")
    prompt: str | None = Field(default=None, description="Generate a one-off case from a prompt")
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    agent_executable: str | None = Field(default=None)
    parallelism: int = Field(default=1, ge=1, le=16)
    iterations: int = Field(default=1, ge=1, le=20)
    timeout_seconds: int = Field(default=1800, ge=1)
    max_turns: int = Field(default=12, ge=1)
    benchmark: bool = Field(default=True)
    extra_args: list[str] = Field(default_factory=list)
    collect_database_trace: bool = Field(default=True)
    llm_judge: bool = Field(default=True)


def _resolve_skill(name: str) -> Path:
    skill_dir = resolve_skill(name)
    if skill_dir is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    return skill_dir


def _run(*, request: RunRequest, validate_only: bool) -> dict[str, object]:
    skill_dir = _resolve_skill(request.skill)
    result = run_evaluation(
        project_root=BACKEND_ROOT,
        skill_dir=str(skill_dir),
        agent=request.agent,
        model=request.model,
        profile=request.profile,
        case_files=request.case,
        prompt=request.prompt,
        executable=request.agent_executable,
        must_contain=request.must_contain,
        must_not_contain=request.must_not_contain,
        parallelism=request.parallelism,
        iterations=request.iterations,
        timeout_seconds=request.timeout_seconds,
        max_turns=request.max_turns,
        benchmark=request.benchmark,
        output_dir=str(RUNS_ROOT),
        extra_args=request.extra_args,
        validate_only=validate_only,
        collect_database_trace=request.collect_database_trace,
        user_id=request.user_id,
        client_task_id=request.client_task_id,
        run_llm_judge_enabled=request.llm_judge,
    )
    return result


@router.post("/run")
def create_run(request: RunRequest) -> dict[str, object]:
    """Queue an evaluation and return immediately with a job id."""
    try:
        skill_dir = _resolve_skill(request.skill)
        return job_manager.submit(request.model_dump(), skill_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jobs")
def list_jobs(user_id: str | None = None) -> list[dict[str, object]]:
    return job_manager.list(user_id=user_id)


@router.get("/capacity")
def get_capacity() -> dict[str, object]:
    """Expose both levels of local evaluation concurrency."""
    return job_manager.capacity()


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, object]:
    job = job_manager.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/validate")
def validate_run(request: RunRequest) -> dict[str, object]:
    """Validate a Skill/eval config without executing the full run."""
    try:
        return _run(request=request, validate_only=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
