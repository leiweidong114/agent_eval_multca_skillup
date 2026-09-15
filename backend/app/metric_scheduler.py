from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_eval.database import search_conversations
from app.config import BACKEND_ROOT
from app.metric_job_manager import metric_job_manager
from app.metrics_store import MetricsStore


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


class MetricScheduler:
    """Periodically enqueue changed, non-evaluation sessions from the last 24h."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return _truthy(os.getenv("SESSION_METRICS_AUTO_ENABLED"))

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

    def _run(self) -> None:
        initial_delay = max(5, int(os.getenv("SESSION_METRICS_AUTO_INITIAL_DELAY_SECONDS", "60")))
        interval = max(300, int(os.getenv("SESSION_METRICS_AUTO_INTERVAL_SECONDS", "3600")))
        if self._stop.wait(initial_delay):
            return
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                LOGGER.exception("Automatic historical-session metric calculation failed")
            if self._stop.wait(interval):
                return

    def run_once(self) -> dict[str, Any] | None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=24)
        max_sessions = min(500, max(1, int(os.getenv("SESSION_METRICS_AUTO_MAX_SESSIONS", "100"))))
        result = search_conversations(
            BACKEND_ROOT,
            source="non_evaluation",
            start_time=start,
            end_time=end,
            limit=max_sessions,
            offset=0,
        )
        conversations = result.get("conversations") or []
        store = MetricsStore()
        statuses = store.statuses(item["root_session_id"] for item in conversations)
        pending: list[str] = []
        for item in conversations:
            session_id = str(item["root_session_id"])
            calculated_at = _as_datetime((statuses.get(session_id) or {}).get("calculated_at"))
            finished_at = _as_datetime(item.get("finished_at"))
            if calculated_at is None or (finished_at is not None and finished_at > calculated_at):
                pending.append(session_id)
        if not pending:
            return None
        return metric_job_manager.submit(
            session_ids=pending,
            user_id=os.getenv("SESSION_METRICS_AUTO_USER_ID", "system"),
            start_time=start,
            end_time=end,
            use_llm_judge=_truthy(os.getenv("SESSION_METRICS_AUTO_USE_LLM_JUDGE", "true")),
        )


metric_scheduler = MetricScheduler()
