from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent_eval.database import resolve_database_config
from app.config import BACKEND_ROOT, RUNS_ROOT


def expired_runs() -> dict[str, Any]:
    days = resolve_database_config(BACKEND_ROOT).retention_days
    cutoff = datetime.now() - timedelta(days=days)
    items = []
    if RUNS_ROOT.is_dir():
        for report in RUNS_ROOT.rglob("evaluation-report.json"):
            path = report.parent
            if "_jobs" not in path.parts and datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                try:
                    data = json.loads(report.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
                items.append({"run_id": data.get("run_id", path.name), "path": str(path), "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat()})
    return {"retention_days": days, "cutoff": cutoff.isoformat(), "expired": items}


def cleanup_expired_runs() -> dict[str, Any]:
    report = expired_runs()
    root = RUNS_ROOT.resolve()
    deleted = []
    for item in report["expired"]:
        target = Path(item["path"]).resolve()
        if root not in target.parents:
            continue
        shutil.rmtree(target)
        deleted.append(item["run_id"])
    return {**report, "deleted": deleted}
