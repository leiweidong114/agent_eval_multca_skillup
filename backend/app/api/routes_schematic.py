from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from agent_eval.database import conversation_filter_options, search_conversation_interactions
from app.config import BACKEND_ROOT


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
            user_id=user_id,
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
def interaction_filters() -> dict[str, Any]:
    try:
        return conversation_filter_options(BACKEND_ROOT)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据库查询失败，请运行 agent-eval check-database 检查连接") from exc


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
