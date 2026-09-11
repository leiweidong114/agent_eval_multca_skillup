from __future__ import annotations

import json
import os
import subprocess
import sys
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from agent_eval.database import (
    enrich_interaction_rows,
    group_interaction_sessions,
    group_subagent_interactions,
    summarize_interaction_rows,
)

from app.auth import employee_from_request
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
def list_runs(request: Request) -> list[dict[str, object]]:
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
        if report.get("user_id") != employee_from_request(request):
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
def get_run(run_id: str, request: Request) -> dict[str, object]:
    """Return a single evaluation report by run_id."""
    found = _find_run(run_id)
    if found:
        return found[1]
    raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")


@router.get("/runs/{run_id}/interactions")
def get_run_interactions(
    run_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=300),
    scope: Literal["all", "main_agent", "subagent"] = Query("all"),
    subagent: str | None = Query(None, max_length=120),
) -> dict[str, object]:
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
        return {"run_id": run_id, "items": [], "trace_file": None, "total": 0, "page": page, "page_size": page_size, "summary": summarize_interaction_rows([]), "filtered_summary": summarize_interaction_rows([]), "sessions": [], "subagents": [], "scope_counts": {"all": 0, "main_agent": 0, "subagent": 0}}
    try:
        items = json.loads(trace.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Model interaction trace is unreadable") from exc
    all_items = items if isinstance(items, list) else []
    all_items = [item for item in all_items if isinstance(item, dict)]
    enrich_interaction_rows(all_items)
    summary = summarize_interaction_rows(all_items)
    term = (search or "").strip().casefold()
    selected_subagent = (subagent or "").strip().casefold()
    filtered = [
        item for item in all_items
        if (scope == "all" or item.get("interaction_scope") == scope)
        and (not selected_subagent or str(item.get("subagent_name") or "").casefold() == selected_subagent)
        and (not term or term in json.dumps(item, ensure_ascii=False, default=str).casefold())
    ]
    start = (page - 1) * page_size
    return {
        "run_id": run_id,
        "items": filtered[start:start + page_size],
        "trace_file": str(trace),
        "total": len(filtered),
        "unfiltered_total": len(all_items),
        "page": page,
        "page_size": page_size,
        "summary": summary,
        "filtered_summary": summarize_interaction_rows(filtered),
        "sessions": group_interaction_sessions(all_items),
        "subagents": group_subagent_interactions(all_items),
        "scope_counts": {
            "all": len(all_items),
            "main_agent": sum(item.get("interaction_scope") == "main_agent" for item in all_items),
            "subagent": sum(item.get("interaction_scope") == "subagent" for item in all_items),
        },
    }


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
