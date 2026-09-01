from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import BACKEND_ROOT


router = APIRouter(prefix="/api/schematic", tags=["schematic"])
SKILL_ROOT = BACKEND_ROOT / "skills" / "schematic-generation"
PROJECTS_ROOT = BACKEND_ROOT / "schematic_projects"


class DiagramRequest(BaseModel):
    title: str = "Untitled schematic"
    components: list[dict[str, Any]]
    connections: list[dict[str, Any]]


class JudgeRequest(BaseModel):
    diagram: DiagramRequest
    schematic: dict[str, Any]


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
