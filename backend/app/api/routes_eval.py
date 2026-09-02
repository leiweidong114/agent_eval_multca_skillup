from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from agent_eval.runner import run_evaluation
from agent_eval.runtime import validate_evaluation_capabilities
from app.config import BACKEND_ROOT, RUNS_ROOT
from app.job_manager import job_manager
from app.skill_registry import compose_skills, resolve_skill

router = APIRouter(prefix="/api", tags=["eval"])


class RunRequest(BaseModel):
    user_id: str = Field(default="local", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
    task_name: str | None = Field(default=None, max_length=200)
    client_task_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
    evaluation_type: str = Field(default="skill", pattern=r"^(skill|schematic)$")
    skill: str | None = Field(default=None, description="Backward-compatible primary Skill")
    skills: list[str] = Field(default_factory=list, max_length=8)
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
    require_model_verification: bool = Field(
        default=True,
        description="Fail the evaluation unless PostgreSQL proves the requested model was called",
    )
    llm_judge: bool = Field(default=True)

    @model_validator(mode="after")
    def normalize_skills(self) -> "RunRequest":
        selected = list(dict.fromkeys(self.skills or ([self.skill] if self.skill else [])))
        if not selected:
            raise ValueError("Select at least one Skill")
        if len(selected) > 8:
            raise ValueError("At most 8 Skills can be evaluated together")
        self.skills = selected
        self.skill = selected[0]
        return self


class BatchTarget(BaseModel):
    agent: str
    model: str
    profile: str


class BatchRunRequest(BaseModel):
    name: str = Field(default="批量评测", min_length=1, max_length=200)
    targets: list[BatchTarget] = Field(min_length=2, max_length=32)
    base_request: dict[str, object]


def _resolve_skill(name: str) -> Path:
    skill_dir = resolve_skill(name)
    if skill_dir is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    return skill_dir


def _resolve_request_skill(request: RunRequest) -> Path:
    if len(request.skills) == 1:
        return _resolve_skill(request.skills[0])
    try:
        return compose_skills(request.skills)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _run(*, request: RunRequest, validate_only: bool) -> dict[str, object]:
    skill_dir = _resolve_request_skill(request)
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
        require_model_verification=request.require_model_verification,
        user_id=request.user_id,
        task_name=request.task_name,
        client_task_id=request.client_task_id,
        run_llm_judge_enabled=request.llm_judge,
        evaluation_type=request.evaluation_type,
        selected_skills=request.skills,
    )
    return result


@router.post("/run")
def create_run(request: RunRequest) -> dict[str, object]:
    """Queue an evaluation and return immediately with a job id."""
    try:
        skill_dir = _resolve_request_skill(request)
        validate_evaluation_capabilities(
            request.agent,
            require_model_selection=request.require_model_verification,
        )
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


@router.post("/batches")
def create_batch(request: BatchRunRequest) -> dict[str, object]:
    """Queue the Cartesian Agent/model combinations as one comparison batch."""
    try:
        normalized: list[RunRequest] = []
        seen: set[tuple[str, str, str]] = set()
        for target in request.targets:
            key = (target.agent, target.model, target.profile)
            if key in seen:
                continue
            seen.add(key)
            run = RunRequest(**request.base_request, **target.model_dump())
            validate_evaluation_capabilities(
                run.agent,
                require_model_selection=run.require_model_verification,
            )
            normalized.append(run)
        if len(normalized) < 2:
            raise ValueError("Batch evaluation requires at least two unique Agent/model combinations")
        skill_dir = _resolve_request_skill(normalized[0])
        return job_manager.submit_batch(
            [item.model_dump() for item in normalized],
            skill_dir,
            name=request.name,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/batches")
def list_batches(user_id: str | None = None) -> list[dict[str, object]]:
    return job_manager.list_batches(user_id=user_id)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, object]:
    batch = job_manager.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


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
