from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import employee_from_request
from app.config import RUNS_ROOT
from app.session_metric_judge import judge_session_metrics
from app.session_task_classifier import classify_session_task


router = APIRouter(prefix="/api/judge-interactions", tags=["judge-interactions"])
AUDIT_ROOT = RUNS_ROOT / "_judge"
RECORDS_ROOT = AUDIT_ROOT / "records"
SAFE_ID = re.compile(r"^[a-f0-9]{32}$")
JUDGE_TYPE_BY_PURPOSE = {
    "evaluation_judge": "task_evaluation",
    "session_metric_judge": "metric_calculation",
    "session_task_classification": "task_classification",
    "schematic_rationality_judge": "schematic_rationality",
}


def _normalized_summary(value: dict[str, Any]) -> dict[str, Any]:
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    return {
        **value,
        "judge_type": value.get("judge_type")
        or JUDGE_TYPE_BY_PURPOSE.get(str(value.get("purpose") or ""), "other"),
        "total_tokens": usage.get("total_tokens", 0),
    }


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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
                rows.append(_normalized_summary(value))
    except OSError:
        return []
    return sorted(rows, key=lambda item: str(item.get("started_at") or ""), reverse=True)


@router.get("")
def list_judge_interactions(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    purpose: str | None = Query(None, max_length=80),
    judge_type: str | None = Query(None, max_length=80),
    model: str | None = Query(None, max_length=300),
    context_id: str | None = Query(None, max_length=200),
    user_id: str | None = Query(None, max_length=120),
    status: str | None = Query(None, max_length=30),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    include_local: bool = Query(True),
) -> dict[str, Any]:
    employee = employee_from_request(request)
    allowed_users = {employee, "local"} if include_local else {employee}
    rows = [row for row in _summaries() if row.get("user_id") in allowed_users]
    if purpose:
        rows = [row for row in rows if row.get("purpose") == purpose]
    if judge_type:
        rows = [row for row in rows if row.get("judge_type") == judge_type]
    if model:
        rows = [row for row in rows if model.casefold() in str(row.get("model") or "").casefold()]
    if context_id:
        rows = [row for row in rows if context_id.casefold() in str(row.get("context_id") or "").casefold()]
    if user_id:
        rows = [row for row in rows if user_id.casefold() in str(row.get("user_id") or "").casefold()]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    if start_time:
        start_time = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
        rows = [row for row in rows if (stamp := _parse_time(row.get("started_at"))) and stamp >= start_time]
    if end_time:
        end_time = end_time if end_time.tzinfo else end_time.replace(tzinfo=timezone.utc)
        rows = [row for row in rows if (stamp := _parse_time(row.get("started_at"))) and stamp < end_time]
    return {
        "items": rows[offset:offset + limit],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }


@router.get("/filters")
def judge_interaction_filters(request: Request, include_local: bool = Query(True)) -> dict[str, Any]:
    employee = employee_from_request(request)
    allowed_users = {employee, "local"} if include_local else {employee}
    rows = [row for row in _summaries() if row.get("user_id") in allowed_users]
    return {
        "judge_types": sorted({str(row.get("judge_type")) for row in rows if row.get("judge_type")}),
        "models": sorted({str(row.get("model")) for row in rows if row.get("model")}),
        "users": sorted({str(row.get("user_id")) for row in rows if row.get("user_id")}),
        "statuses": sorted({str(row.get("status")) for row in rows if row.get("status")}),
    }


@router.post("/test")
def test_judge_availability(request: Request) -> dict[str, Any]:
    """Exercise the same classification and metric Judge paths used by session metrics."""
    employee = employee_from_request(request)
    started = time.perf_counter()
    context_id = f"judge-self-test-{uuid.uuid4().hex[:12]}"
    stamp = datetime.now(timezone.utc).isoformat()
    conversation = {
        "root_session_id": context_id,
        "timeline": [
            {
                "request_id": uuid.uuid4().hex,
                "start_time": stamp,
                "end_time": stamp,
                "status": "success",
                "proxy_server_request": {
                    "body": {
                        "messages": [
                            {
                                "role": "user",
                                "content": "请生成一个包含电源、MCU和LED驱动的简易智能路灯原理图。",
                            }
                        ]
                    }
                },
                "messages": [
                    {
                        "role": "user",
                        "content": "请生成一个包含电源、MCU和LED驱动的简易智能路灯原理图。",
                    }
                ],
                "response": {
                    "role": "assistant",
                    "content": "这是 Judge 可用性测试的模拟会话响应。",
                },
                "metadata": {"request_purpose": "judge_self_test"},
            }
        ],
    }
    classification = classify_session_task(conversation, employee_no=employee)
    metrics = judge_session_metrics(conversation, employee_no=employee)
    classification_ok = classification.get("status") == "completed"
    metrics_ok = metrics.get("status") == "completed"
    ok = classification_ok and metrics_ok
    return {
        "ok": ok,
        "status": "ok" if ok else "failed",
        "message": (
            "Judge 模型已通过任务分类和会话指标两项真实 JSON 推理测试"
            if ok
            else "Judge 模型未通过完整可用性测试，请查看失败步骤"
        ),
        "context_id": context_id,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "checks": {
            "task_classification": classification,
            "session_metric_judge": metrics,
        },
        "limitations": [
            "该测试使用短会话，不能排除真实长会话的上下文长度限制",
            "测试通过后仍可能因后续额度耗尽、限流或上游临时故障而失败",
        ],
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
    value["judge_type"] = value.get("judge_type") or JUDGE_TYPE_BY_PURPOSE.get(
        str(value.get("purpose") or ""), "other"
    )
    return value
