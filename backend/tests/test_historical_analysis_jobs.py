from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from app.historical_analysis_jobs import HistoricalAnalysisJobManager


def test_classification_jobs_use_configured_parallelism_and_round_robin_models(monkeypatch):
    saved: list[dict] = []
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        "app.historical_analysis_jobs.load_runtime_settings",
        lambda _root: {"judge_models": ["judge-a", "judge-b"], "judge_parallelism": 3},
    )
    monkeypatch.setattr(
        "app.historical_analysis_jobs.get_conversation",
        lambda _root, *, root_session_id, **_kwargs: {
            "root_session_id": root_session_id,
            "timeline": [],
            "interaction_count": 1,
        },
    )

    def classify(conversation, *, model_override=None, **_kwargs):
        calls.append((conversation["root_session_id"], model_override))
        return {
            "status": "completed",
            "task_type": "other",
            "task_category": "other",
            "task_subtype": None,
            "model": model_override,
        }

    class FakeStore:
        def save_analysis_result(self, **kwargs):
            saved.append(kwargs)

    monkeypatch.setattr("app.historical_analysis_jobs.classify_session_task", classify)
    monkeypatch.setattr("app.historical_analysis_jobs.first_user_prompt", lambda _conversation: "first prompt")
    monkeypatch.setattr("app.historical_analysis_jobs.MetricsStore", FakeStore)

    manager = HistoricalAnalysisJobManager()
    now = datetime.now(timezone.utc)
    job = manager.submit(
        task_kind="classification",
        session_ids=["s1", "s2", "s3", "s4"],
        user_id="tester",
        start_time=now - timedelta(days=1),
        end_time=now,
        use_llm_judge=True,
    )
    deadline = time.time() + 3
    while time.time() < deadline:
        job = manager.get(job["job_id"])
        if job and job["status"] not in {"queued", "running"}:
            break
        time.sleep(0.01)

    assert job is not None
    assert job["status"] == "completed"
    assert job["parallelism"] == 3
    assert job["completed"] == 4
    assert {session: model for session, model in calls} == {
        "s1": "judge-a", "s2": "judge-b", "s3": "judge-a", "s4": "judge-b",
    }
    assert len(saved) == 4
    assert len(manager.list()) == 1
    assert manager.list()[0]["job_id"] == job["job_id"]
    assert all(item.get("duration_ms") is not None for item in job["per_session"].values())
    assert not any(
        event.get("stage") == "judge_request_progress"
        for item in saved
        for event in item["process_trace"]["events"]
    )
    assert all(item["task_kind"] == "classification" for item in saved)
    assert all(
        any(event["stage"] == "first_prompt_selected" for event in item["process_trace"]["events"])
        for item in saved
    )
