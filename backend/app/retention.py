from __future__ import annotations

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
        for path in RUNS_ROOT.iterdir():
            if path.is_dir() and path.name != "_jobs" and datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                items.append({"run_id": path.name, "path": str(path), "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat()})
    return {"retention_days": days, "cutoff": cutoff.isoformat(), "expired": items}


def cleanup_expired_runs() -> dict[str, Any]:
    report = expired_runs()
    root = RUNS_ROOT.resolve()
    deleted = []
    for item in report["expired"]:
        target = Path(item["path"]).resolve()
        if target.parent != root:
            continue
        shutil.rmtree(target)
        deleted.append(item["run_id"])
    return {**report, "deleted": deleted}
