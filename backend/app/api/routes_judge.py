from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import employee_from_request
from app.config import RUNS_ROOT


router = APIRouter(prefix="/api/judge-interactions", tags=["judge-interactions"])
AUDIT_ROOT = RUNS_ROOT / "_judge"
RECORDS_ROOT = AUDIT_ROOT / "records"
SAFE_ID = re.compile(r"^[a-f0-9]{32}$")


def _summaries() -> list[dict[str, Any]]:
    index = AUDIT_ROOT / "index.jsonl"
    if not index.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in index.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
                value["total_tokens"] = usage.get("total_tokens", 0)
                rows.append(value)
    except OSError:
        return []
    return sorted(rows, key=lambda item: str(item.get("started_at") or ""), reverse=True)


@router.get("")
def list_judge_interactions(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    purpose: str | None = Query(None, max_length=80),
    model: str | None = Query(None, max_length=300),
    context_id: str | None = Query(None, max_length=200),
    include_local: bool = Query(True),
) -> dict[str, Any]:
    employee = employee_from_request(request)
    allowed_users = {employee, "local"} if include_local else {employee}
    rows = [row for row in _summaries() if row.get("user_id") in allowed_users]
    if purpose:
        rows = [row for row in rows if row.get("purpose") == purpose]
    if model:
        rows = [row for row in rows if model.casefold() in str(row.get("model") or "").casefold()]
    if context_id:
        rows = [row for row in rows if context_id.casefold() in str(row.get("context_id") or "").casefold()]
    return {
        "items": rows[offset:offset + limit],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{interaction_id}")
def get_judge_interaction(interaction_id: str, request: Request) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(interaction_id):
        raise HTTPException(status_code=404, detail="Judge interaction not found")
    path = (RECORDS_ROOT / f"{interaction_id}.json").resolve()
    if path.parent != RECORDS_ROOT.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Judge interaction not found")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Judge interaction is unreadable") from exc
    employee = employee_from_request(request)
    if value.get("user_id") not in {employee, "local"}:
        raise HTTPException(status_code=404, detail="Judge interaction not found")
    return value
