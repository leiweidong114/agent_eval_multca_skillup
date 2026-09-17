from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from agent_eval.database import (
    conversation_filter_options,
    get_conversation,
    get_interaction_detail,
    search_conversation_interactions,
    search_conversations,
)
from app.auth import employee_from_request
from app.config import BACKEND_ROOT
from app.response_cache import cache_key, get_cached_json, set_cached_json


router = APIRouter(prefix="/api/schematic", tags=["schematic"])
# Deterministic diagram preview, separate from the four-Skill LLM evaluation.
SKILL_ROOT = BACKEND_ROOT / "schematic_demo"
PROJECTS_ROOT = BACKEND_ROOT / "schematic_projects"


class DiagramRequest(BaseModel):
    title: str = "Untitled schematic"
    components: list[dict[str, Any]]
    connections: list[dict[str, Any]]


class JudgeRequest(BaseModel):
    diagram: DiagramRequest
    schematic: dict[str, Any]


@router.get("/interactions")
def search_interactions(
    request: Request,
    user_id: str | None = None,
    end_user: str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    try:
        return search_conversation_interactions(
            BACKEND_ROOT,
            user_id=employee_from_request(request),
            end_user=end_user,
            session_id=session_id,
            model=model,
            limit=limit,
            offset=offset,
            full_content=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据库查询失败，请运行 agent-eval check-database 检查连接") from exc


@router.get("/interaction-filters")
def interaction_filters(request: Request) -> dict[str, Any]:
    try:
        employee_from_request(request)
        return conversation_filter_options(BACKEND_ROOT)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据库查询失败，请运行 agent-eval check-database 检查连接") from exc


@router.get("/conversations")
def conversation_list(
    request: Request,
    end_user: str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    interaction_count: int | None = Query(None, ge=1),
    source: str = Query("all", pattern="^(all|evaluation|non_evaluation)$"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    key = cache_key("schematic-conversations-v2", {
        "end_user": end_user, "session_id": session_id, "model": model,
        "interaction_count": interaction_count,
        "source": source, "limit": limit, "offset": offset,
        "start_time": start_time, "end_time": end_time,
    })
    cached = get_cached_json(key)
    if cached is not None:
        cached["cache"] = "hit"
        return cached
    try:
        result = search_conversations(
            BACKEND_ROOT,
            user_id=None,
            end_user=end_user,
            session_id=session_id,
            model=model,
            interaction_count=interaction_count,
            source=source,
            limit=limit,
            offset=offset,
            start_time=start_time,
            end_time=end_time,
        )
        result["cache"] = "miss"
        set_cached_json(key, result, ttl_seconds=30)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据库查询失败，请运行 agent-eval check-database 检查连接") from exc


@router.get("/conversations/{root_session_id}")
def conversation_detail(
    request: Request,
    root_session_id: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    key = cache_key("schematic-conversation-detail-v2", {
        "session_id": root_session_id, "start_time": start_time, "end_time": end_time,
    })
    cached = get_cached_json(key)
    if cached is not None:
        cached["cache"] = "hit"
        return cached
    try:
        employee_from_request(request)
        result = get_conversation(
            BACKEND_ROOT,
            root_session_id=root_session_id,
            user_id=None,
            include_content=False,
            start_time=start_time,
            end_time=end_time,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据库查询失败，请运行 agent-eval check-database 检查连接") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权查看")
    result["cache"] = "miss"
    set_cached_json(key, result, ttl_seconds=30)
    return result


@router.get("/interactions/{request_id}")
def interaction_detail(request: Request, request_id: str) -> dict[str, Any]:
    """Load the large request/response payload only after a user expands one turn."""
    key = cache_key("schematic-interaction-v1", {"request_id": request_id})
    cached = get_cached_json(key)
    if cached is not None:
        cached["cache"] = "hit"
        return cached
    try:
        employee_from_request(request)
        result = get_interaction_detail(BACKEND_ROOT, request_id=request_id, user_id=None)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据库查询失败，请运行 agent-eval check-database 检查连接") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="模型交互不存在或无权查看")
    result["cache"] = "miss"
    set_cached_json(key, result, ttl_seconds=300)
    return result


def _project(project_id: str) -> Path:
    if not project_id.isalnum():
        raise HTTPException(status_code=400, detail="Invalid project id")
    path = (PROJECTS_ROOT / project_id).resolve()
    if path.parent != PROJECTS_ROOT.resolve():
        raise HTTPException(status_code=400, detail="Invalid project path")
    return path


def _command(script: str, input_path: Path, output: Path, extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL_ROOT / "scripts" / script), "--input", str(input_path), "--output", str(output), *(extra or [])],
        cwd=str(SKILL_ROOT), capture_output=True, text=True, timeout=120, check=False,
    )


@router.get("/example")
def get_example() -> dict[str, Any]:
    return json.loads((SKILL_ROOT / "assets" / "example_block_diagram.json").read_text(encoding="utf-8"))


@router.post("/generate")
def generate(request: DiagramRequest) -> dict[str, Any]:
    project_id = uuid.uuid4().hex
    root = _project(project_id)
    output = root / "generated"
    root.mkdir(parents=True, exist_ok=False)
    input_path = root / "block_diagram.json"
    input_path.write_text(json.dumps(request.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    process = _command("schematic_pipeline.py", input_path, output)
    if process.returncode != 0:
        raise HTTPException(status_code=422, detail=process.stderr.strip() or process.stdout.strip())
    judge = _command("schematic_judge.py", input_path, output)
    try:
        report = json.loads(judge.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise HTTPException(status_code=500, detail=f"Judge returned invalid output: {judge.stderr}") from exc
    (root / "judge-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "project_id": project_id, "project_url": f"/schematic?project={project_id}",
        "judge": report, "schematic": json.loads((output / "schematic.json").read_text(encoding="utf-8")),
    }


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    root = _project(project_id)
    schematic = root / "generated" / "schematic.json"
    if not schematic.is_file():
        raise HTTPException(status_code=404, detail="Schematic project not found")
    return {
        "project_id": project_id, "project_url": f"/schematic?project={project_id}",
        "input": json.loads((root / "block_diagram.json").read_text(encoding="utf-8")),
        "schematic": json.loads(schematic.read_text(encoding="utf-8")),
        "judge": json.loads((root / "judge-report.json").read_text(encoding="utf-8")),
    }


@router.post("/judge")
def judge(request: JudgeRequest) -> dict[str, Any]:
    """Score an externally generated schematic JSON against its source block diagram."""
    project_id = "judge" + uuid.uuid4().hex
    root = _project(project_id)
    root.mkdir(parents=True, exist_ok=False)
    input_path = root / "block_diagram.json"
    candidate = root / "candidate-schematic.json"
    input_path.write_text(json.dumps(request.diagram.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    candidate.write_text(json.dumps(request.schematic, ensure_ascii=False, indent=2), encoding="utf-8")
    process = _command("schematic_judge.py", input_path, root, ["--schematic", str(candidate)])
    try:
        return json.loads(process.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=process.stderr or "Judge failed") from exc
