from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from agent_eval.database import conversation_time_window, search_conversations
from app.auth import employee_from_request
from app.config import BACKEND_ROOT
from app.infrastructure_config import infrastructure_health
from app.historical_analysis_jobs import historical_analysis_jobs
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
    task_kind: Literal["classification", "metrics", "conversation_judge"] = "metrics"

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
        "scheduler": metric_scheduler.status(),
    }


@router.get("/sessions")
def sessions(
    request: Request,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    end_user: str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    task_classification: str | None = Query(
        None,
        pattern="^(schematic_generation|block_to_schematic|block_to_signal_list|signal_list_to_schematic|schematic_apply_to_tianshu|schematic_adjustment|other_schematic|other)$",
    ),
    metric_status: str = Query("all", pattern="^(all|calculated|uncalculated)$"),
    refresh: bool = False,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    employee_from_request(request)
    key = cache_key("metric-sessions-v2", {
        "start_time": start_time, "end_time": end_time, "end_user": end_user,
        "session_id": session_id, "model": model, "metric_status": metric_status,
        "task_classification": task_classification,
        "limit": limit, "offset": offset,
    })
    cached = None if refresh else get_cached_json(key)
    if cached is not None:
        cached["cache"] = "hit"
        return cached
    try:
        store = MetricsStore()
        all_statuses: dict[str, dict[str, Any]] | None = None
        allowed_session_ids: set[str] | None = None
        excluded_session_ids: set[str] | None = None
        if metric_status != "all":
            all_statuses = store.all_statuses()
            calculated_ids = set(all_statuses)
            if metric_status == "calculated":
                allowed_session_ids = calculated_ids
            else:
                excluded_session_ids = calculated_ids
        if task_classification:
            classified_ids = store.session_ids_for_task_classification(task_classification)
            allowed_session_ids = classified_ids if allowed_session_ids is None else allowed_session_ids & classified_ids
            if excluded_session_ids:
                allowed_session_ids -= excluded_session_ids
                excluded_session_ids = None
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
        try:
            statuses = store.statuses(item["root_session_id"] for item in result["conversations"])
        except Exception:
            statuses = all_statuses or {}
        for item in result["conversations"]:
            metric = statuses.get(item["root_session_id"])
            item["metric_status"] = metric.get("status") if metric else "not_calculated"
            item["metric_calculated_at"] = metric.get("calculated_at") if metric else None
            item["metric_definition_version"] = metric.get("metric_definition_version") if metric else None
            item["quality_rates"] = metric.get("quality_rates") if metric else {}
            item["task_type"] = metric.get("task_type") if metric else None
            item["task_category"] = metric.get("task_category") if metric else None
            item["task_subtype"] = metric.get("task_subtype") if metric else None
            item["classification_status"] = metric.get("classification_status") if metric else "not_calculated"
            item["conversation_judge_status"] = metric.get("conversation_judge_status") if metric else "not_calculated"
        result["metric_status_filter"] = metric_status
        result["task_classification_filter"] = task_classification
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
        return historical_analysis_jobs.submit(
            task_kind=payload.task_kind,
            session_ids=payload.session_ids,
            user_id=employee_from_request(request),
            start_time=payload.start_time,
            end_time=payload.end_time,
            use_llm_judge=payload.use_llm_judge,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/jobs")
def jobs(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    employee_from_request(request)
    items = historical_analysis_jobs.list(limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/jobs/{job_id}")
def job(request: Request, job_id: str) -> dict[str, Any]:
    employee_from_request(request)
    result = historical_analysis_jobs.get(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="指标计算任务不存在")
    return result


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel_job(request: Request, job_id: str) -> dict[str, Any]:
    """Cooperatively cancel classification, metrics, or conversation-Judge work."""
    employee_from_request(request)
    result = historical_analysis_jobs.cancel(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="历史会话分析任务不存在或后端已重启")
    return result


@router.post("/scheduler/run", status_code=202)
def run_scheduler_now(request: Request) -> dict[str, Any]:
    """Run the same changed-session discovery used by the hourly scheduler."""
    employee_from_request(request)
    try:
        return metric_scheduler.run_once()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/aggregate/quality")
def quality_aggregate(request: Request) -> dict[str, Any]:
    employee_from_request(request)
    try:
        result = MetricsStore().get_quality_aggregate()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="累计质量指标尚未计算")
    return result


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


@router.get("/{session_id}/process")
def metric_process(
    request: Request,
    session_id: str,
    task_kind: Literal["classification", "metrics", "conversation_judge"] = "metrics",
) -> dict[str, Any]:
    employee_from_request(request)
    try:
        store = MetricsStore()
        result = (
            store.get_analysis_process(session_id, task_kind)
            if hasattr(store, "get_analysis_process")
            else store.get_process_trace(session_id)
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="该会话没有已保存的计算过程；旧任务需重新计算")
    return result


@router.get("/{session_id}/quality-records")
def quality_records(request: Request, session_id: str) -> dict[str, Any]:
    """Return complete original resultText for each quality record, never event previews."""
    employee_from_request(request)
    try:
        records = MetricsStore().quality_records(session_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "session_id": session_id,
        "count": len(records),
        "records": [{"_id": str(row.get("_id") or ""), "uuid": row.get("uuid"),
                     "checkType": str(row.get("checkType") or "").strip(),
                     "createTime": row.get("createTime"), "resultText": row.get("resultText")}
                    for row in records],
    }
