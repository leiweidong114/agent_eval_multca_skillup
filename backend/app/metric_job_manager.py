from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from agent_eval.database import get_conversation
from app.config import BACKEND_ROOT
from app.metrics_store import MetricsStore
from app.session_metrics import calculate_rule_metrics
from app.session_metric_judge import judge_session_metrics


class MetricJobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="session-metrics")

    def submit(
        self,
        *,
        session_ids: list[str],
        user_id: str,
        start_time: datetime,
        end_time: datetime,
        use_llm_judge: bool,
    ) -> dict[str, Any]:
        store = MetricsStore()
        now = datetime.now(timezone.utc)
        job = {
            "job_id": f"metrics-{uuid.uuid4().hex}",
            "status": "queued",
            "total": len(session_ids),
            "completed": 0,
            "failed": 0,
            "session_ids": session_ids,
            "use_llm_judge": use_llm_judge,
            "user_id": user_id,
            "start_time": start_time,
            "end_time": end_time,
            "errors": [],
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job["job_id"]] = job
        store.save_job(job)
        self._executor.submit(self._run, job["job_id"], store)
        return self._public(job)

    def _save(self, job: dict[str, Any], store: MetricsStore) -> None:
        job["updated_at"] = datetime.now(timezone.utc)
        store.save_job(job)

    def _run(self, job_id: str, store: MetricsStore) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            self._save(job, store)
        for session_id in list(job["session_ids"]):
            try:
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
                result = calculate_rule_metrics(conversation)
                if job["use_llm_judge"]:
                    result["judge"] = judge_session_metrics(
                        conversation, employee_no=str(job.get("user_id") or "") or None
                    )
                    if result["judge"].get("status") == "completed":
                        result["metrics"]["suspected_fabrication_count"] = len(
                            result["judge"].get("suspected_fabrications") or []
                        )
                        # Rule-derived task type stays authoritative when it has
                        # explicit Skill/script markers. Judge classification fills
                        # only the low-confidence fallback.
                        if result.get("task_type_source") == "rule_fallback":
                            result["task_type"] = result["judge"].get("task_type") or result["task_type"]
                            result["task_type_source"] = "llm_judge"
                        result["status"] = "completed"
                    else:
                        result["status"] = "rules_completed_judge_unavailable"
                else:
                    result["judge"] = {"status": "disabled"}
                store.upsert_metrics(result)
                with self._lock:
                    job["completed"] += 1
            except Exception as exc:
                with self._lock:
                    job["failed"] += 1
                    job["errors"] = [*job["errors"], {"session_id": session_id, "detail": str(exc)}][-100:]
            with self._lock:
                self._save(job, store)
        with self._lock:
            job["status"] = "completed" if not job["failed"] else "completed_with_errors"
            job["finished_at"] = datetime.now(timezone.utc)
            self._save(job, store)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return self._public(job)
        try:
            job = MetricsStore().get_job(job_id)
        except Exception:
            return None
        return self._public(job) if job else None

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if key != "_id"}


metric_job_manager = MetricJobManager()
