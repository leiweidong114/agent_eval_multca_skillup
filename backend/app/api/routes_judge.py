from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import employee_from_request
from app.config import readable_runs_roots
from app.session_metric_judge import judge_session_metrics
from app.session_task_classifier import classify_session_task


router = APIRouter(prefix="/api/judge-interactions", tags=["judge-interactions"])
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
    rows: list[dict[str, Any]] = []
    for root in readable_runs_roots():
        index = root / "_judge" / "index.jsonl"
        if not index.is_file():
            continue
        try:
            for line in index.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    rows.append(_normalized_summary(value))
        except OSError:
            continue
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


def _run_judge_availability(
    employee: str,
    progress_callback: Callable[[str, str, str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Exercise production Judge paths and report real request/retry progress."""
    def progress(phase: str, stage: str, message: str, **details: Any) -> None:
        if progress_callback:
            progress_callback(phase, stage, message, details)

    def gateway_progress(phase: str) -> Callable[[str, dict[str, Any]], None]:
        def report(stage: str, details: dict[str, Any]) -> None:
            label = "任务分类" if phase == "classification" else "会话指标"
            messages = {
                "model_resolved": f"{label}：已确定 Judge 模型和网关",
                "request_started": f"{label}：第 {details.get('attempt')} / {details.get('max_attempts')} 次模型请求中",
                "response_received": f"{label}：网关返回 HTTP {details.get('status_code')}",
                "request_error": f"{label}：请求异常 {details.get('error_type')}",
                "retry_wait": f"{label}：等待 {details.get('wait_seconds')} 秒后重试",
                "request_failed": f"{label}：模型请求失败",
                "response_parsing_started": f"{label}：正在解析模型响应",
                "response_format_fallback": f"{label}：JSON 模式不兼容，改用普通请求重试",
                "response_parsed": f"{label}：模型响应已解析为 JSON",
                "judge_failed": f"{label}：Judge 调用失败",
            }
            progress(phase, stage, messages.get(stage, f"{label}：{stage}"), **details)
        return report

    started = time.perf_counter()
    context_id = f"judge-self-test-{uuid.uuid4().hex[:12]}"
    progress("setup", "prepared", "已创建短会话测试数据", context_id=context_id)
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
    progress("classification", "started", "开始任务分类 Judge 真实推理")
    classification_started = time.perf_counter()
    classification_kwargs = (
        {"progress_callback": gateway_progress("classification")} if progress_callback else {}
    )
    classification = classify_session_task(
        conversation, employee_no=employee, **classification_kwargs,
    )
    progress(
        "classification", "completed" if classification.get("status") == "completed" else "failed",
        "任务分类 Judge 测试完成" if classification.get("status") == "completed" else "任务分类 Judge 测试失败",
        duration_ms=round((time.perf_counter() - classification_started) * 1000, 2),
        result=classification,
    )
    progress("metrics", "started", "开始会话指标 Judge 真实推理")
    metrics_started = time.perf_counter()
    metrics_kwargs = (
        {
            "progress_callback": lambda stage, index, total, message: progress(
                "metrics", stage, message, chunk_index=index, chunk_total=total,
            ),
            "request_progress_callback": gateway_progress("metrics"),
        }
        if progress_callback else {}
    )
    metrics = judge_session_metrics(
        conversation, employee_no=employee, **metrics_kwargs,
    )
    progress(
        "metrics", "completed" if metrics.get("status") == "completed" else "failed",
        "会话指标 Judge 测试完成" if metrics.get("status") == "completed" else "会话指标 Judge 测试失败",
        duration_ms=round((time.perf_counter() - metrics_started) * 1000, 2),
        result=metrics,
    )
    classification_ok = classification.get("status") == "completed"
    metrics_ok = metrics.get("status") == "completed"
    ok = classification_ok and metrics_ok
    progress("finish", "completed" if ok else "failed", "两项 Judge 测试已结束", ok=ok)
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


@router.post("/test")
def test_judge_availability(request: Request) -> dict[str, Any]:
    """Backward-compatible synchronous availability check."""
    return _run_judge_availability(employee_from_request(request))


class JudgeTestJobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="judge-self-test")

    def start(self, employee: str) -> dict[str, Any]:
        job_id = f"judge-test-{uuid.uuid4().hex}"
        with self._lock:
            existing = next(
                (
                    value for value in self._jobs.values()
                    if value["employee"] == employee and value["status"] in {"queued", "running"}
                ),
                None,
            )
            if existing is not None:
                return deepcopy({key: value for key, value in existing.items() if key != "employee"})
            if len(self._jobs) >= 100:
                finished = [key for key, value in self._jobs.items() if value["status"] in {"completed", "failed"}]
                for key in finished[:50]:
                    self._jobs.pop(key, None)
            job = {
                "job_id": job_id,
                "employee": employee,
                "status": "queued",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "started_at": None,
                "finished_at": None,
                "events": [],
                "checks": {},
                "result": None,
            }
            self._jobs[job_id] = job
        self._executor.submit(self._run, job_id)
        return self.get(job_id, employee) or {}

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = datetime.now(timezone.utc).isoformat()
            employee = job["employee"]
        started = time.perf_counter()

        def update(phase: str, stage: str, message: str, details: dict[str, Any]) -> None:
            with self._lock:
                job = self._jobs[job_id]
                job["events"].append({
                    "sequence": len(job["events"]) + 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                    "phase": phase,
                    "stage": stage,
                    "message": message,
                    "details": details,
                })
                if phase in {"classification", "metrics"} and stage in {"completed", "failed"}:
                    job["checks"]["task_classification" if phase == "classification" else "session_metric_judge"] = details.get("result")

        try:
            result = _run_judge_availability(employee, update)
        except Exception as exc:
            update("finish", "failed", "Judge 测试运行异常", {"error": str(exc)})
            result = {
                "ok": False,
                "status": "failed",
                "message": "Judge 可用性测试运行失败",
                "error": str(exc),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "checks": job["checks"],
            }
        with self._lock:
            job = self._jobs[job_id]
            job["result"] = result
            job["status"] = "completed" if result.get("ok") else "failed"
            job["finished_at"] = datetime.now(timezone.utc).isoformat()

    def get(self, job_id: str, employee: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["employee"] != employee:
                return None
            return deepcopy({key: value for key, value in job.items() if key != "employee"})


judge_test_jobs = JudgeTestJobManager()


@router.post("/test-jobs", status_code=202)
def start_judge_test_job(request: Request) -> dict[str, Any]:
    return judge_test_jobs.start(employee_from_request(request))


@router.get("/test-jobs/{job_id}")
def get_judge_test_job(job_id: str, request: Request) -> dict[str, Any]:
    job = judge_test_jobs.get(job_id, employee_from_request(request))
    if job is None:
        raise HTTPException(status_code=404, detail="Judge test job not found")
    return job


@router.get("/{interaction_id}")
def get_judge_interaction(interaction_id: str, request: Request) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(interaction_id):
        raise HTTPException(status_code=404, detail="Judge interaction not found")
    path = None
    for root in readable_runs_roots():
        records_root = (root / "_judge" / "records").resolve()
        candidate = (records_root / f"{interaction_id}.json").resolve()
        if candidate.parent == records_root and candidate.is_file():
            path = candidate
            break
    if path is None:
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
