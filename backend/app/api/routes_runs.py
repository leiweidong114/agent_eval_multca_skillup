from __future__ import annotations

import json
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


@router.get("/runs")
def list_runs(user_id: str | None = None) -> list[dict[str, object]]:
    """List evaluation run directories with a report, newest first."""
    if not RUNS_ROOT.is_dir():
        return []
    entries: list[dict[str, object]] = []
    for run_dir in sorted(RUNS_ROOT.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        report = _load_report(run_dir)
        if report is None:
            continue
        if user_id is not None and report.get("user_id") != user_id:
            continue
        entries.append(
            {
                "run_id": run_dir.name,
                "task_id": report.get("task_id", run_dir.name),
                "user_id": report.get("user_id"),
                "result_dir": str(run_dir),
                "report": report,
            }
        )
    return entries


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, object]:
    """Return a single evaluation report by run_id."""
    run_dir = (RUNS_ROOT / run_id).resolve()
    if not run_dir.is_dir() or run_dir.parent != RUNS_ROOT.resolve():
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    report = _load_report(run_dir)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No report for run: {run_id}")
    return report
