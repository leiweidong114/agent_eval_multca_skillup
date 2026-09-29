from __future__ import annotations

from datetime import datetime, timezone

from app.metric_scheduler import MetricScheduler
from app.session_metrics import METRIC_DEFINITION_VERSION


def _conversation(session_id: str, finished_at: str) -> dict:
    return {"root_session_id": session_id, "finished_at": finished_at}


def test_scheduler_uses_previous_complete_hour_and_submits_independent_jobs(monkeypatch):
    calls: list[dict] = []
    searched: dict = {}

    def search(_root, **kwargs):
        searched.update(kwargs)
        return {
            "conversations": [
                _conversation("done", "2026-09-29T04:10:00+00:00"),
                _conversation("new", "2026-09-29T04:20:00+00:00"),
                _conversation("old-metric", "2026-09-29T04:30:00+00:00"),
            ],
            "scan_truncated": False,
        }

    class Store:
        def statuses(self, _session_ids):
            return {
                "done": {
                    "classification_status": "completed",
                    "metrics_status": "completed",
                    "calculated_at": "2026-09-29T04:50:00+00:00",
                    "metric_definition_version": METRIC_DEFINITION_VERSION,
                },
                "old-metric": {
                    "classification_status": "completed",
                    "metrics_status": "completed",
                    "calculated_at": "2026-09-29T04:40:00+00:00",
                    "metric_definition_version": "older-version",
                },
            }

    def submit(**kwargs):
        calls.append(kwargs)
        return {"job_id": f"job-{kwargs['task_kind']}", "status": "queued"}

    monkeypatch.setattr("app.metric_scheduler.search_conversations", search)
    monkeypatch.setattr("app.metric_scheduler.MetricsStore", Store)
    monkeypatch.setattr("app.metric_scheduler.historical_analysis_jobs.submit", submit)
    monkeypatch.setenv("SESSION_METRICS_AUTO_MAX_SESSIONS", "0")
    monkeypatch.setenv("SESSION_METRICS_AUTO_USE_LLM_JUDGE", "true")

    result = MetricScheduler().run_once(now=datetime(2026, 9, 29, 5, 37, tzinfo=timezone.utc))

    assert searched["start_time"] == datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc)
    assert searched["end_time"] == datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc)
    assert searched["source"] == "non_evaluation"
    assert searched["unbounded_scan"] is True
    assert result["status"] == "submitted"
    assert result["discovered_sessions"] == 3
    assert result["classification_pending"] == 1
    assert result["metrics_pending"] == 2
    assert calls[0]["task_kind"] == "classification"
    assert calls[0]["session_ids"] == ["new"]
    assert calls[1]["task_kind"] == "metrics"
    assert calls[1]["session_ids"] == ["new", "old-metric"]


def test_scheduler_still_submits_metrics_when_classification_submit_fails(monkeypatch):
    monkeypatch.setattr(
        "app.metric_scheduler.search_conversations",
        lambda *_args, **_kwargs: {
            "conversations": [_conversation("new", "2026-09-29T04:20:00+00:00")],
            "scan_truncated": False,
        },
    )

    class Store:
        def statuses(self, _session_ids):
            return {}

    submitted: list[str] = []

    def submit(**kwargs):
        submitted.append(kwargs["task_kind"])
        if kwargs["task_kind"] == "classification":
            raise ValueError("未配置 Judge 模型")
        return {"job_id": "metrics-job", "status": "queued"}

    monkeypatch.setattr("app.metric_scheduler.MetricsStore", Store)
    monkeypatch.setattr("app.metric_scheduler.historical_analysis_jobs.submit", submit)

    scheduler = MetricScheduler()
    result = scheduler.run_once(now=datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc))

    assert submitted == ["classification", "metrics"]
    assert result["status"] == "partial"
    assert result["jobs"]["classification"] is None
    assert result["jobs"]["metrics"]["job_id"] == "metrics-job"
    assert result["errors"] == [{"task_kind": "classification", "detail": "未配置 Judge 模型"}]
    assert scheduler.status()["last_error"] == "未配置 Judge 模型"


def test_scheduler_returns_idle_when_hour_has_no_sessions(monkeypatch):
    monkeypatch.setattr(
        "app.metric_scheduler.search_conversations",
        lambda *_args, **_kwargs: {"conversations": [], "scan_truncated": False},
    )

    class Store:
        def statuses(self, session_ids):
            assert list(session_ids) == []
            return {}

    monkeypatch.setattr("app.metric_scheduler.MetricsStore", Store)
    result = MetricScheduler().run_once(now=datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc))

    assert result["status"] == "idle"
    assert result["classification_pending"] == 0
    assert result["metrics_pending"] == 0
