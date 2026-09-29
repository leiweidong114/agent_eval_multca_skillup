from __future__ import annotations

import copy
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_eval.database import search_conversations
from app.config import BACKEND_ROOT
from app.historical_analysis_jobs import historical_analysis_jobs
from app.metrics_store import MetricsStore
from app.session_metrics import METRIC_DEFINITION_VERSION


LOGGER = logging.getLogger(__name__)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _hour_floor(value: datetime) -> datetime:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


class MetricScheduler:
    """At every clock hour, enqueue analyses for the previous complete hour."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._next_run_at: datetime | None = None
        self._last_started_at: datetime | None = None
        self._last_finished_at: datetime | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return _truthy(os.getenv("SESSION_METRICS_AUTO_ENABLED"))

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "enabled": self.enabled,
                "running": bool(self._thread and self._thread.is_alive()),
                "schedule": "hourly_on_the_hour",
                "window": "previous_complete_hour",
                "next_run_at": self._next_run_at,
                "last_started_at": self._last_started_at,
                "last_finished_at": self._last_finished_at,
                "last_error": self._last_error,
                "last_result": copy.deepcopy(self._last_result),
            }

    def start(self) -> None:
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="session-metric-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=3)
        with self._state_lock:
            self._next_run_at = None

    def _run(self) -> None:
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            next_run = _hour_floor(now) + timedelta(hours=1)
            with self._state_lock:
                self._next_run_at = next_run
            if self._stop.wait(max(0.0, (next_run - now).total_seconds())):
                return
            try:
                self.run_once(now=next_run)
            except Exception as exc:
                with self._state_lock:
                    self._last_error = str(exc)
                    self._last_finished_at = datetime.now(timezone.utc)
                LOGGER.exception("Automatic hourly historical-session analysis failed")

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        window_end = _hour_floor(now or datetime.now(timezone.utc))
        window_start = window_end - timedelta(hours=1)
        with self._state_lock:
            self._last_started_at = datetime.now(timezone.utc)
            self._last_error = None

        configured_max = int(os.getenv("SESSION_METRICS_AUTO_MAX_SESSIONS", "0") or "0")
        # Zero means no session-level cap. The database layer still retains its
        # raw-row safety guard and reports scan_truncated when that guard is hit.
        query_limit = configured_max if configured_max > 0 else 10000
        result = search_conversations(
            BACKEND_ROOT,
            source="non_evaluation",
            start_time=window_start,
            end_time=window_end,
            limit=query_limit,
            offset=0,
            unbounded_scan=True,
        )
        conversations = result.get("conversations") or []
        session_ids = list(dict.fromkeys(
            str(item.get("root_session_id") or "").strip()
            for item in conversations
            if str(item.get("root_session_id") or "").strip()
        ))
        statuses = MetricsStore().statuses(session_ids)
        classification_pending: list[str] = []
        metrics_pending: list[str] = []
        for item in conversations:
            session_id = str(item.get("root_session_id") or "").strip()
            if not session_id:
                continue
            saved = statuses.get(session_id) or {}
            if saved.get("classification_status") != "completed":
                classification_pending.append(session_id)

            calculated_at = _as_datetime(saved.get("calculated_at"))
            finished_at = _as_datetime(item.get("finished_at"))
            version_changed = saved.get("metric_definition_version") != METRIC_DEFINITION_VERSION
            metrics_completed = saved.get("metrics_status") == "completed"
            if (
                not metrics_completed
                or calculated_at is None
                or version_changed
                or (finished_at is not None and finished_at > calculated_at)
            ):
                metrics_pending.append(session_id)

        user_id = os.getenv("SESSION_METRICS_AUTO_USER_ID", "system")
        use_llm_judge = _truthy(os.getenv("SESSION_METRICS_AUTO_USE_LLM_JUDGE", "true"))
        jobs: dict[str, dict[str, Any] | None] = {"classification": None, "metrics": None}
        errors: list[dict[str, str]] = []
        for task_kind, pending in (
            ("classification", classification_pending),
            ("metrics", metrics_pending),
        ):
            if not pending:
                continue
            try:
                jobs[task_kind] = historical_analysis_jobs.submit(
                    task_kind=task_kind,
                    session_ids=list(dict.fromkeys(pending)),
                    user_id=user_id,
                    start_time=window_start,
                    end_time=window_end,
                    use_llm_judge=use_llm_judge,
                )
            except Exception as exc:
                errors.append({"task_kind": task_kind, "detail": str(exc)})
                LOGGER.exception("Could not submit hourly %s job", task_kind)

        response = {
            "status": "partial" if errors else ("submitted" if any(jobs.values()) else "idle"),
            "message": "整点会话分析任务已提交" if any(jobs.values()) else "上一完整小时没有待处理会话",
            "window": {"start_time": window_start, "end_time": window_end},
            "discovered_sessions": len(session_ids),
            "classification_pending": len(set(classification_pending)),
            "metrics_pending": len(set(metrics_pending)),
            "scan_truncated": bool(result.get("scan_truncated")),
            "jobs": jobs,
            "errors": errors,
        }
        with self._state_lock:
            self._last_result = copy.deepcopy(response)
            self._last_finished_at = datetime.now(timezone.utc)
            self._last_error = "; ".join(item["detail"] for item in errors) or None
        return response


metric_scheduler = MetricScheduler()
