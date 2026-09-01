from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent_eval.runner import run_evaluation
from app.config import BACKEND_ROOT, RUNS_ROOT, SKILLS_ROOT

router = APIRouter(prefix="/api", tags=["eval"])


class RunRequest(BaseModel):
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


def _resolve_skill(name: str) -> Path:
    skill_dir = (SKILLS_ROOT / name).resolve()
    if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
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
    )
    return result


@router.post("/run")
def create_run(request: RunRequest) -> dict[str, object]:
    """Trigger a full evaluation run (with skill + optional baseline)."""
    try:
        return _run(request=request, validate_only=False)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
