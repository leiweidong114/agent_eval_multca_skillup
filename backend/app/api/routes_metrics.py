from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from agent_eval.database import conversation_time_window, search_conversations
from app.auth import employee_from_request
from app.config import BACKEND_ROOT
from app.infrastructure_config import infrastructure_health
from app.metric_job_manager import metric_job_manager
from app.metric_scheduler import metric_scheduler
from app.metrics_store import MetricsStore, metrics_store_health
from app.response_cache import response_cache_health
from app.response_cache import cache_key, get_cached_json, set_cached_json
from app.schematic_data_client import schematic_data_health


router = APIRouter(prefix="/api/session-metrics", tags=["session-metrics"])


class CalculateMetricsRequest(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=100)
    start_time: datetime
    end_time: datetime
    use_llm_judge: bool = True

    @model_validator(mode="after")
    def validate_window(self) -> "CalculateMetricsRequest":
        conversation_time_window(self.start_time, self.end_time)
        self.session_ids = list(dict.fromkeys(value.strip() for value in self.session_ids if value.strip()))
        if not self.session_ids:
            raise ValueError("Select at least one session")
        return self


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    employee_from_request(request)
    return {
        "configuration": infrastructure_health(),
        "store": metrics_store_health(),
        "cache": response_cache_health(),
        "schematic_data_api": schematic_data_health(),
        "scheduler": {"enabled": metric_scheduler.enabled},
    }


@router.get("/sessions")
def sessions(
    request: Request,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    end_user: str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    metric_status: str = Query("all", pattern="^(all|calculated|uncalculated)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    employee_from_request(request)
    key = cache_key("metric-sessions-v1", {
        "start_time": start_time, "end_time": end_time, "end_user": end_user,
        "session_id": session_id, "model": model, "metric_status": metric_status,
        "limit": limit, "offset": offset,
    })
    cached = get_cached_json(key)
    if cached is not None:
        cached["cache"] = "hit"
        return cached
    try:
        store: MetricsStore | None = None
        all_statuses: dict[str, dict[str, Any]] | None = None
        allowed_session_ids: set[str] | None = None
        excluded_session_ids: set[str] | None = None
        if metric_status != "all":
            store = MetricsStore()
            all_statuses = store.all_statuses()
            calculated_ids = set(all_statuses)
            if metric_status == "calculated":
                allowed_session_ids = calculated_ids
            else:
                excluded_session_ids = calculated_ids
        result = search_conversations(
            BACKEND_ROOT,
            source="non_evaluation",
            end_user=end_user,
            session_id=session_id,
            model=model,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            offset=offset,
            allowed_root_session_ids=allowed_session_ids,
            excluded_root_session_ids=excluded_session_ids,
        )
        if all_statuses is not None:
            statuses = all_statuses
        else:
            try:
                statuses = MetricsStore().statuses(item["root_session_id"] for item in result["conversations"])
            except Exception:
                statuses = {}
        for item in result["conversations"]:
            metric = statuses.get(item["root_session_id"])
            item["metric_status"] = metric.get("status") if metric else "not_calculated"
            item["metric_calculated_at"] = metric.get("calculated_at") if metric else None
            item["metric_definition_version"] = metric.get("metric_definition_version") if metric else None
        result["metric_status_filter"] = metric_status
        result["cache"] = "miss"
        set_cached_json(key, result, ttl_seconds=60)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="历史会话查询失败，请检查LiteLLM数据库连接") from exc


@router.get("")
def metric_results(
    request: Request,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    task_type: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    end_user: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    employee_from_request(request)
    start, end = conversation_time_window(start_time, end_time)
    try:
        result = MetricsStore().list_metrics(
            start_time=start,
            end_time=end,
            task_type=task_type,
            agent=agent,
            model=model,
            end_user=end_user,
            limit=limit,
            offset=offset,
        )
        result["query_window"] = {"start_time": start, "end_time": end, "default_hours": 24}
        return result
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/summary")
def metric_summary(
    request: Request,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    employee_from_request(request)
    start, end = conversation_time_window(start_time, end_time)
    try:
        return MetricsStore().summary(start_time=start, end_time=end)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/jobs", status_code=202)
def calculate(request: Request, payload: CalculateMetricsRequest) -> dict[str, Any]:
    try:
        return metric_job_manager.submit(
            session_ids=payload.session_ids,
            user_id=employee_from_request(request),
            start_time=payload.start_time,
            end_time=payload.end_time,
            use_llm_judge=payload.use_llm_judge,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
def job(request: Request, job_id: str) -> dict[str, Any]:
    employee_from_request(request)
    result = metric_job_manager.get(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="指标计算任务不存在")
    return result


@router.post("/scheduler/run", status_code=202)
def run_scheduler_now(request: Request) -> dict[str, Any]:
    """Run the same changed-session discovery used by the hourly scheduler."""
    employee_from_request(request)
    try:
        result = metric_scheduler.run_once()
        return result or {"status": "idle", "message": "最近24小时没有需要重新计算的会话"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{session_id}")
def metric_detail(request: Request, session_id: str) -> dict[str, Any]:
    employee_from_request(request)
    try:
        result = MetricsStore().get_metrics(session_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="会话指标不存在")
    return result
