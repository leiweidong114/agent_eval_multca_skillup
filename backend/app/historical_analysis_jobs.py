from __future__ import annotations

import copy
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

from agent_eval.database import get_conversation
from agent_eval.model_config import load_runtime_settings
from app.config import BACKEND_ROOT
from app.metric_job_manager import metric_job_manager
from app.metrics_store import MetricsStore
from app.session_metric_judge import judge_session_metrics
from app.session_task_classifier import classify_session_task, first_user_prompt


TASK_KINDS = {"classification", "metrics", "conversation_judge"}
TERMINAL_STATUSES = {"completed", "completed_with_errors", "failed"}


class HistoricalAnalysisJobManager:
    """Run independently selectable historical-session analyses in parallel."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._dispatcher = ThreadPoolExecutor(max_workers=8, thread_name_prefix="historical-analysis")

    def submit(
        self,
        *,
        task_kind: str,
        session_ids: list[str],
        user_id: str,
        start_time: datetime,
        end_time: datetime,
        use_llm_judge: bool,
    ) -> dict[str, Any]:
        if task_kind not in TASK_KINDS:
            raise ValueError(f"不支持的历史会话任务：{task_kind}")
        settings = load_runtime_settings(BACKEND_ROOT)
        models = [str(item).strip() for item in settings.get("judge_models") or [] if str(item).strip()]
        if not models and settings.get("judge_model"):
            models = [str(settings["judge_model"])]
        if task_kind in {"classification", "conversation_judge"} and not models:
            raise ValueError("请先在设置页至少选择一个 Judge 模型")
        parallelism = max(1, min(32, int(settings.get("judge_parallelism") or 2)))
        now = datetime.now(timezone.utc)
        job = {
            "job_id": f"historical-{task_kind}-{uuid.uuid4().hex}",
            "task_kind": task_kind,
            "status": "queued",
            "phase": "queued",
            "total": len(session_ids),
            "completed": 0,
            "failed": 0,
            "progress": 0,
            "session_ids": list(session_ids),
            "user_id": user_id,
            "start_time": start_time,
            "end_time": end_time,
            "use_llm_judge": use_llm_judge,
            "judge_models": models,
            "parallelism": min(parallelism, len(session_ids)),
            "events": [],
            "event_seq": 0,
            "results": {},
            "per_session": {
                session_id: {
                    "session_id": session_id,
                    "status": "queued",
                    "assigned_model": models[index % len(models)] if models else None,
                    "events": [],
                }
                for index, session_id in enumerate(session_ids)
            },
            "errors": [],
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job["job_id"]] = job
        self._dispatcher.submit(self._run, job["job_id"])
        return self._public(job)

    def _event(self, job: dict[str, Any], session_id: str | None, stage: str, message: str, **details: Any) -> None:
        with self._lock:
            job["event_seq"] += 1
            event = {
                "sequence": job["event_seq"],
                "timestamp": datetime.now(timezone.utc),
                "task_kind": job["task_kind"],
                "session_id": session_id,
                "stage": stage,
                "message": message,
                **details,
            }
            job["events"] = [*(job.get("events") or []), event][-2000:]
            if session_id and session_id in job["per_session"]:
                session = job["per_session"][session_id]
                session["events"] = [*(session.get("events") or []), event][-500:]
            job["updated_at"] = datetime.now(timezone.utc)

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["phase"] = "parallel_processing"
        self._event(job, None, "job_started", "历史会话任务已开始并行执行", outcome="running")
        with ThreadPoolExecutor(max_workers=job["parallelism"], thread_name_prefix=job["task_kind"]) as pool:
            futures = {
                pool.submit(self._run_session, job, session_id): session_id
                for session_id in job["session_ids"]
            }
            for future in as_completed(futures):
                session_id = futures[future]
                try:
                    result = future.result()
                    with self._lock:
                        job["completed"] += 1
                        job["results"][session_id] = result
                        job["per_session"][session_id]["status"] = "completed"
                except Exception as exc:
                    with self._lock:
                        job["failed"] += 1
                        job["errors"].append({"session_id": session_id, "detail": str(exc)})
                        job["per_session"][session_id]["status"] = "failed"
                        job["per_session"][session_id]["error"] = str(exc)
                    self._event(job, session_id, "session_failed", "该会话任务执行失败", outcome="failed", detail=str(exc))
                finished_at = datetime.now(timezone.utc)
                with self._lock:
                    session = job["per_session"][session_id]
                    session["finished_at"] = finished_at
                    started_at = session.get("started_at")
                    session["duration_ms"] = max(
                        0,
                        int((finished_at - started_at).total_seconds() * 1000),
                    ) if isinstance(started_at, datetime) else None
                    job["progress"] = round((job["completed"] + job["failed"]) / max(1, job["total"]) * 100)
        with self._lock:
            job["status"] = "completed" if not job["failed"] else "completed_with_errors"
            job["phase"] = "completed"
            job["progress"] = 100
            job["finished_at"] = datetime.now(timezone.utc)
        self._event(job, None, "job_completed", "历史会话任务执行结束", outcome=job["status"])

    def _run_session(self, job: dict[str, Any], session_id: str) -> dict[str, Any]:
        assigned_model = job["per_session"][session_id].get("assigned_model")
        with self._lock:
            job["per_session"][session_id]["status"] = "running"
            job["per_session"][session_id]["started_at"] = datetime.now(timezone.utc)
        self._event(job, session_id, "session_loading", "正在读取 LiteLLM 完整会话", outcome="running")
        conversation = get_conversation(
            BACKEND_ROOT,
            root_session_id=session_id,
            user_id=None,
            include_content=True,
            start_time=job["start_time"],
            end_time=job["end_time"],
        )
        if conversation is None:
            raise ValueError("会话不存在于所选时间范围")
        self._event(
            job, session_id, "session_loaded", "会话读取成功", outcome="success",
            output={
                "interaction_count": conversation.get("interaction_count", len(conversation.get("timeline") or [])),
                "agent": conversation.get("agent"),
                "models": conversation.get("models") or [],
                "end_user": conversation.get("end_user"),
            },
        )
        if job["task_kind"] == "metrics":
            return self._run_metrics(job, session_id, assigned_model)
        if job["task_kind"] == "classification":
            prompt = first_user_prompt(conversation)
            self._event(
                job, session_id, "first_prompt_selected",
                "仅提取第一条用户 Prompt，不对会话分片", outcome="success",
                input={"first_user_prompt": prompt, "prompt_found": bool(prompt)},
                model=assigned_model,
            )
            result = classify_session_task(
                conversation, employee_no=job["user_id"],
                progress_callback=None, model_override=assigned_model,
            )
        else:
            callback = self._judge_callback(job, session_id)
            result = judge_session_metrics(
                conversation, employee_no=job["user_id"],
                progress_callback=lambda stage, index, total, message: self._event(
                    job, session_id, stage, message, outcome="running",
                    chunk_index=index, chunk_total=total, model=assigned_model,
                ),
                request_progress_callback=callback,
                model_override=assigned_model,
            )
        succeeded = result.get("status") in {"completed", "not_applicable"}
        self._event(
            job, session_id,
            "analysis_completed" if succeeded else "analysis_unavailable",
            "模型分析完成" if succeeded else "模型分析失败",
            outcome="success" if succeeded else "failed", output=result, model=assigned_model,
        )
        process = self._session_process(job, session_id, result)
        MetricsStore().save_analysis_result(
            task_kind=job["task_kind"], session_id=session_id,
            result=result, process_trace=process, user_id=job["user_id"],
        )
        self._event(job, session_id, "result_persisted", "结果和独立过程已写入 MongoDB", outcome="success")
        if not succeeded:
            raise RuntimeError(str(result.get("error") or result.get("reason") or "Judge 返回不可用"))
        return result

    def _run_metrics(self, job: dict[str, Any], session_id: str, assigned_model: str | None) -> dict[str, Any]:
        self._event(job, session_id, "metrics_started", "规则指标与质量指标计算已启动", outcome="running", model=assigned_model)
        child = metric_job_manager.submit(
            session_ids=[session_id], user_id=job["user_id"],
            start_time=job["start_time"], end_time=job["end_time"],
            use_llm_judge=job["use_llm_judge"], judge_model=assigned_model,
            externally_limited=True,
        )
        child_job_id = child["job_id"]
        seen = 0
        while True:
            current = metric_job_manager.get(child_job_id)
            if current is None:
                raise RuntimeError("内部指标任务丢失")
            events = current.get("events") or []
            for event in events[seen:]:
                self._event(
                    job, session_id, str(event.get("stage") or "metrics_progress"),
                    str(event.get("message") or "指标计算处理中"),
                    **{key: value for key, value in event.items() if key not in {"sequence", "timestamp", "session_id", "stage", "message"}},
                )
            seen = len(events)
            if current.get("status") in TERMINAL_STATUSES:
                if current.get("failed"):
                    raise RuntimeError(str((current.get("errors") or [{}])[-1].get("detail") or "指标计算失败"))
                result = MetricsStore().get_metrics(session_id)
                if result is None:
                    raise RuntimeError("指标任务完成但未读取到持久化结果")
                return result
            time.sleep(0.2)

    def _judge_callback(self, job: dict[str, Any], session_id: str) -> Callable[[str, dict[str, Any]], None]:
        def callback(stage: str, details: dict[str, Any]) -> None:
            self._event(job, session_id, f"judge_{stage}", "Judge 模型调用进度", outcome="running", output=details)
        return callback

    def _session_process(self, job: dict[str, Any], session_id: str, result: dict[str, Any]) -> dict[str, Any]:
        session = job["per_session"][session_id]
        return {
            "job_id": job["job_id"],
            "task_kind": job["task_kind"],
            "session_id": session_id,
            "status": str(result.get("status") or "completed"),
            "user_id": job["user_id"],
            "assigned_model": session.get("assigned_model"),
            "created_at": session.get("started_at"),
            "finished_at": datetime.now(timezone.utc),
            "events": list(session.get("events") or []),
            "result_summary": result,
        }

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._public(job) if job else None

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda item: item.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            return [self._public(job) for job in jobs[: max(1, min(200, limit))]]

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(job)
        value["server_time"] = datetime.now(timezone.utc)
        return value


historical_analysis_jobs = HistoricalAnalysisJobManager()
