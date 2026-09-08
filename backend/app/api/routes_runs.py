from __future__ import annotations

import json
import os
import subprocess
import sys
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import RUNS_ROOT

router = APIRouter(prefix="/api", tags=["runs"])


def _load_report(run_dir: Path) -> dict[str, object] | None:
    report_file = run_dir / "evaluation-report.json"
    if not report_file.is_file():
        return None
    try:
        return json.loads(report_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _find_run(run_id: str) -> tuple[Path, dict[str, object]] | None:
    root = RUNS_ROOT.resolve()
    for report_file in root.rglob("evaluation-report.json") if root.is_dir() else []:
        run_dir = report_file.parent.resolve()
        if root not in run_dir.parents:
            continue
        report = _load_report(run_dir)
        if report is not None and str(report.get("run_id")) == run_id:
            return run_dir, report
    return None


def _find_run_dir(run_id: str) -> Path | None:
    found = _find_run(run_id)
    if found:
        return found[0]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        return None
    root = RUNS_ROOT.resolve()
    matches = [
        path.resolve()
        for path in root.rglob(f"*__{run_id}") if root.is_dir() and path.is_dir()
        if root in path.resolve().parents
    ]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


@router.get("/runs")
def list_runs(user_id: str | None = None) -> list[dict[str, object]]:
    """List evaluation run directories with a report, newest first."""
    if not RUNS_ROOT.is_dir():
        return []
    entries: list[dict[str, object]] = []
    report_files = sorted(
        RUNS_ROOT.rglob("evaluation-report.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for report_file in report_files:
        run_dir = report_file.parent
        report = _load_report(run_dir)
        if report is None:
            continue
        if user_id is not None and report.get("user_id") != user_id:
            continue
        entries.append(
            {
                "run_id": report.get("run_id", run_dir.name),
                "task_id": report.get("task_id", report.get("run_id", run_dir.name)),
                "user_id": report.get("user_id", run_dir.parents[1].name),
                "task_name": report.get("task_name", run_dir.parent.name),
                "result_dir": str(run_dir),
                "report": report,
            }
        )
    return entries


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, object]:
    """Return a single evaluation report by run_id."""
    found = _find_run(run_id)
    if found:
        return found[1]
    raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")


@router.get("/runs/{run_id}/interactions")
def get_run_interactions(run_id: str) -> dict[str, object]:
    """Return the durable, full LiteLLM request/response records for a run."""
    found = _find_run(run_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    run_dir, report = found
    configured = report.get("database_trace_file")
    trace = Path(str(configured)).resolve() if configured else run_dir / "model-interactions.json"
    if trace != run_dir / "model-interactions.json" and run_dir not in trace.parents:
        raise HTTPException(status_code=400, detail="Unsafe interaction trace path")
    if not trace.is_file():
        return {"run_id": run_id, "items": [], "trace_file": None}
    try:
        items = json.loads(trace.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Model interaction trace is unreadable") from exc
    return {"run_id": run_id, "items": items if isinstance(items, list) else [], "trace_file": str(trace)}


@router.post("/runs/{run_id}/open-folder")
def open_run_folder(run_id: str) -> dict[str, object]:
    """Open this local evaluation's artifact directory in the OS file manager."""
    run_dir = _find_run_dir(run_id)
    if not run_dir:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    try:
        if sys.platform == "win32":
            os.startfile(str(run_dir))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(run_dir)])
        else:
            subprocess.Popen(["xdg-open", str(run_dir)])
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to open result directory: {exc}") from exc
    return {"opened": True, "run_id": run_id, "path": str(run_dir)}
