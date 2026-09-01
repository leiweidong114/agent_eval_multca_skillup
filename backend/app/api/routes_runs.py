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
def list_runs() -> list[dict[str, object]]:
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
        entries.append(
            {
                "run_id": report.get("run_id", run_dir.name),
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
    root = RUNS_ROOT.resolve()
    for report_file in root.rglob("evaluation-report.json") if root.is_dir() else []:
        run_dir = report_file.parent.resolve()
        if root not in run_dir.parents:
            continue
        report = _load_report(run_dir)
        if report is not None and str(report.get("run_id")) == run_id:
            return report
    raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
