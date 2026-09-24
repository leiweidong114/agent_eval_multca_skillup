from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, model_validator

from agent_eval.runner import run_evaluation
from agent_eval.runtime import validate_evaluation_capabilities
from agent_eval.model_config import load_runtime_settings
from agent_eval.cli_catalog import SCHEMATIC_PIPELINE_SKILLS
from agent_eval.evaluators import list_evaluators as installed_evaluators
from agent_eval.evaluators import resolve_evaluator
from agent_eval.schematic_tasks import (
    DEFAULT_SCHEMATIC_TASK_TYPE,
    list_schematic_task_types,
)
from app.config import BACKEND_ROOT, readable_runs_roots, runs_root
from app.auth import employee_from_request
from app.job_manager import job_manager
from app.skill_registry import compose_skills, resolve_skill

router = APIRouter(prefix="/api", tags=["eval"])

MAX_EVALUATION_INPUT_FILES = 20
MAX_EVALUATION_INPUT_BYTES = 25 * 1024 * 1024
UPLOAD_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


class EvaluationInputRef(BaseModel):
    upload_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    filename: str = Field(min_length=1, max_length=255)


def _resolve_evaluation_inputs(
    references: list[EvaluationInputRef], employee_no: str
) -> list[dict[str, object]]:
    """Resolve opaque upload IDs without trusting a browser-provided path."""
    resolved: list[dict[str, object]] = []
    for reference in references:
        if not UPLOAD_ID_PATTERN.fullmatch(reference.upload_id):
            raise ValueError("Invalid evaluation input upload id")
        upload_dir = next(
            (
                root / "_uploads" / employee_no / reference.upload_id
                for root in readable_runs_roots()
                if (root / "_uploads" / employee_no / reference.upload_id / "manifest.json").is_file()
            ),
            None,
        )
        if upload_dir is None:
            raise FileNotFoundError(f"Uploaded evaluation input was not found: {reference.filename}")
        manifest = json.loads((upload_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("employee_no") != employee_no:
            raise ValueError("Uploaded evaluation input belongs to another user")
        if manifest.get("filename") != reference.filename:
            raise ValueError("Uploaded evaluation input filename does not match its manifest")
        payload = upload_dir / "payload"
        if not payload.is_file():
            raise FileNotFoundError(f"Uploaded evaluation input is incomplete: {reference.filename}")
        resolved.append({
            "upload_id": reference.upload_id,
            "filename": reference.filename,
            "size": int(manifest.get("size") or payload.stat().st_size),
            "sha256": str(manifest.get("sha256") or ""),
            "source_path": str(payload.resolve()),
        })
    return resolved


def _run_payload(request: "RunRequest", employee_no: str) -> dict[str, object]:
    payload = request.model_dump()
    payload["user_id"] = employee_no
    payload["input_file_paths"] = _resolve_evaluation_inputs(
        request.input_files, employee_no
    )
    return payload


class RunRequest(BaseModel):
    user_id: str = Field(default="local", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
    task_name: str | None = Field(default=None, max_length=200)
    client_task_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
    evaluation_type: str = Field(default="skill", pattern=r"^(skill|schematic)$")
    schematic_task_type: str | None = Field(
        default=None,
        pattern=r"^(block_to_schematic|block_to_signal_list|signal_list_to_schematic)$",
    )
    evaluator_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,62}$",
        description="Installed evaluator id; omitted uses the .env default",
    )
    skill: str | None = Field(default=None, description="Backward-compatible primary Skill")
    skills: list[str] = Field(default_factory=list)
    agent: str = Field(..., description="Multica Agent backend name")
    model: str | None = Field(default=None, description="Optional profile model override")
    profile: str | None = Field(default=None, description="Profile from config/models.yaml")
    case: list[str] = Field(default_factory=list, description="Case YAML file paths")
    prompt: str | None = Field(default=None, description="Generate a one-off case from a prompt")
    input_files: list[EvaluationInputRef] = Field(
        default_factory=list,
        max_length=MAX_EVALUATION_INPUT_FILES,
        description="Previously uploaded files copied into the evaluation workspace",
    )
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    agent_executable: str | None = Field(default=None)
    justdo_transport: str = Field(default="auto", pattern=r"^(auto|cli|http)$")
    parallelism: int = Field(default=1, ge=1, le=16)
    iterations: int = Field(default=1, ge=1, le=20)
    timeout_seconds: int = Field(default=1800, ge=1)
    max_turns: int = Field(default=60, ge=1)
    benchmark: bool = Field(
        default=False,
        description="Deprecated compatibility field; without-Skill baseline runs are disabled",
    )
    extra_args: list[str] = Field(default_factory=list)
    collect_database_trace: bool = Field(default=True)
    require_model_verification: bool = Field(
        default=True,
        description="Fail the evaluation unless PostgreSQL proves the requested model was called",
    )
    llm_judge: bool = Field(default=True)

    @model_validator(mode="after")
    def normalize_skills(self) -> "RunRequest":
        # Accept old clients that still send benchmark=true, but never schedule
        # a second without_skill Agent session.
        self.benchmark = False
        if self.evaluation_type == "schematic":
            self.schematic_task_type = self.schematic_task_type or DEFAULT_SCHEMATIC_TASK_TYPE
        elif self.schematic_task_type is not None:
            raise ValueError("schematic_task_type is only valid for schematic evaluations")
        selected = list(dict.fromkeys(self.skills or ([self.skill] if self.skill else [])))
        if self.evaluation_type == "schematic" and not selected:
            return self
        if not selected:
            raise ValueError("Select at least one Skill")
        self.skills = selected
        self.skill = selected[0]
        return self


def _apply_schematic_skill_settings(request: RunRequest) -> RunRequest:
    """Resolve a schematic task's Skill pipeline and evaluator from local settings."""
    if request.evaluation_type != "schematic":
        return request
    task_type = request.schematic_task_type or DEFAULT_SCHEMATIC_TASK_TYPE
    configured = load_runtime_settings(BACKEND_ROOT)
    profiles = configured.get("schematic_task_profiles") or {}
    profile = profiles.get(task_type) if isinstance(profiles, dict) else None
    profile = profile if isinstance(profile, dict) else {}
    if not request.skills:
        selected = profile.get("skills") or configured.get("schematic_skills")
        selected = list(selected) if isinstance(selected, list) else list(SCHEMATIC_PIPELINE_SKILLS)
        request.skills = selected
        request.skill = selected[0]
    if request.evaluator_id is None:
        request.evaluator_id = str(profile.get("evaluator_id") or "schematic-default")
    return request


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
    request = _apply_schematic_skill_settings(request)
    skill_dir = _resolve_request_skill(request)
    result = run_evaluation(
        project_root=BACKEND_ROOT,
        skill_dir=str(skill_dir),
        agent=request.agent,
        model=request.model,
        profile=request.profile,
        case_files=request.case,
        prompt=request.prompt,
        input_files=_resolve_evaluation_inputs(request.input_files, request.user_id),
        executable=request.agent_executable,
        must_contain=request.must_contain,
        must_not_contain=request.must_not_contain,
        parallelism=request.parallelism,
        iterations=request.iterations,
        timeout_seconds=request.timeout_seconds,
        max_turns=request.max_turns,
        benchmark=False,
        output_dir=str(runs_root()),
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
        evaluator_id=request.evaluator_id,
        schematic_task_type=request.schematic_task_type,
        justdo_transport=request.justdo_transport,
    )
    return result


@router.post("/evaluation-inputs")
async def upload_evaluation_input(
    request: Request, file: UploadFile = File(...)
) -> dict[str, object]:
    """Stage one opaque, user-owned file for a later asynchronous evaluation."""
    employee_no = employee_from_request(request)
    filename = Path(str(file.filename or "input.bin")).name.strip()
    if not filename or filename in {".", ".."} or len(filename) > 255:
        raise HTTPException(status_code=400, detail="上传文件名无效或超过 255 个字符")
    upload_id = uuid.uuid4().hex
    upload_dir = runs_root() / "_uploads" / employee_no / upload_id
    upload_dir.mkdir(parents=True, exist_ok=False)
    payload_path = upload_dir / "payload"
    digest = hashlib.sha256()
    size = 0
    try:
        with payload_path.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_EVALUATION_INPUT_BYTES:
                    raise ValueError("单个评测输入文件不能超过 25 MB")
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise ValueError("不能上传空文件")
        manifest = {
            "upload_id": upload_id,
            "employee_no": employee_no,
            "filename": filename,
            "content_type": file.content_type or "application/octet-stream",
            "size": size,
            "sha256": digest.hexdigest(),
        }
        (upload_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {key: manifest[key] for key in (
            "upload_id", "filename", "content_type", "size", "sha256"
        )}
    except (OSError, ValueError) as exc:
        for child in upload_dir.glob("*"):
            child.unlink(missing_ok=True)
        upload_dir.rmdir()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()


@router.post("/run")
def create_run(request: RunRequest, http_request: Request) -> dict[str, object]:
    """Queue an evaluation and return immediately with a job id."""
    try:
        request = _apply_schematic_skill_settings(request)
        resolve_evaluator(
            BACKEND_ROOT,
            evaluation_type=request.evaluation_type,
            evaluator_id=request.evaluator_id,
            schematic_task_type=request.schematic_task_type,
        )
        skill_dir = _resolve_request_skill(request)
        validate_evaluation_capabilities(
            request.agent,
            require_model_selection=request.require_model_verification,
        )
        payload = _run_payload(request, employee_from_request(http_request))
        return job_manager.submit(payload, skill_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jobs")
def list_jobs(request: Request) -> list[dict[str, object]]:
    return job_manager.list(user_id=employee_from_request(request))


@router.get("/evaluators")
def list_evaluators() -> list[dict[str, object]]:
    """List built-in, bundled, project-local, and configured external evaluators."""
    return installed_evaluators(BACKEND_ROOT)


@router.get("/schematic-task-types")
def get_schematic_task_types() -> list[dict[str, str]]:
    return list_schematic_task_types()


@router.get("/capacity")
def get_capacity() -> dict[str, object]:
    """Expose both levels of local evaluation concurrency."""
    return job_manager.capacity()


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, object]:
    job = job_manager.get(job_id)
    if job is None or job.get("user_id") != employee_from_request(request):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/batches")
def create_batch(request: BatchRunRequest, http_request: Request) -> dict[str, object]:
    """Queue the Cartesian Agent/model combinations as one comparison batch."""
    try:
        employee_no = employee_from_request(http_request)
        normalized: list[RunRequest] = []
        seen: set[tuple[str, str, str]] = set()
        for target in request.targets:
            key = (target.agent, target.model, target.profile)
            if key in seen:
                continue
            seen.add(key)
            run = RunRequest(**request.base_request, **target.model_dump())
            run = run.model_copy(update={"user_id": employee_no})
            run = _apply_schematic_skill_settings(run)
            resolve_evaluator(
                BACKEND_ROOT,
                evaluation_type=run.evaluation_type,
                evaluator_id=run.evaluator_id,
                schematic_task_type=run.schematic_task_type,
            )
            validate_evaluation_capabilities(
                run.agent,
                require_model_selection=run.require_model_verification,
            )
            normalized.append(run)
        if len(normalized) < 2:
            raise ValueError("Batch evaluation requires at least two unique Agent/model combinations")
        skill_dir = _resolve_request_skill(normalized[0])
        return job_manager.submit_batch(
            [_run_payload(item, employee_no) for item in normalized],
            skill_dir,
            name=request.name,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/batches")
def list_batches(request: Request) -> list[dict[str, object]]:
    return job_manager.list_batches(user_id=employee_from_request(request))


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, request: Request) -> dict[str, object]:
    batch = job_manager.get_batch(batch_id)
    if batch is None or batch.get("user_id") != employee_from_request(request):
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str, request: Request) -> dict[str, object]:
    existing = job_manager.get_batch(batch_id)
    if existing is None or existing.get("user_id") != employee_from_request(request):
        raise HTTPException(status_code=404, detail="Batch not found")
    batch = job_manager.cancel_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.post("/batches/{batch_id}/prioritize")
def prioritize_batch(batch_id: str, request: Request) -> dict[str, object]:
    existing = job_manager.get_batch(batch_id)
    if existing is None or existing.get("user_id") != employee_from_request(request):
        raise HTTPException(status_code=404, detail="Batch not found")
    batch = job_manager.prioritize_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict[str, object]:
    existing = job_manager.get(job_id)
    if existing is None or existing.get("user_id") != employee_from_request(request):
        raise HTTPException(status_code=404, detail="Job not found")
    job = job_manager.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/status")
def get_job_status(job_id: str, request: Request) -> dict[str, object]:
    """Return only scheduling state, avoiding large completed report payloads."""
    job = job_manager.get(job_id)
    if job is None or job.get("user_id") != employee_from_request(request):
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        key: job.get(key)
        for key in (
            "job_id", "task_id", "status", "phase", "progress", "message",
            "created_at", "started_at", "updated_at", "user_id", "task_name",
            "evaluation_type", "schematic_task_type", "agent", "model", "failure", "error",
        )
    }


@router.post("/jobs/{job_id}/prioritize")
def prioritize_job(job_id: str, request: Request) -> dict[str, object]:
    existing = job_manager.get(job_id)
    if existing is None or existing.get("user_id") != employee_from_request(request):
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        job = job_manager.prioritize(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/validate")
def validate_run(request: RunRequest, http_request: Request) -> dict[str, object]:
    """Validate a Skill/eval config without executing the full run."""
    try:
        request = request.model_copy(
            update={"user_id": employee_from_request(http_request)}
        )
        request = _apply_schematic_skill_settings(request)
        return _run(request=request, validate_only=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
