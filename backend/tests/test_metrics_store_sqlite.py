from datetime import datetime, timezone

from app import metrics_store


class Settings:
    def __init__(self, path):
        self.metrics_sqlite_path = path


def test_sqlite_metrics_and_jobs_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(
        metrics_store,
        "load_infrastructure_settings",
        lambda: Settings(tmp_path / "metrics.sqlite3"),
    )
    store = metrics_store.MetricsStore()
    result = {
        "session_id": "session-1",
        "status": "completed",
        "started_at": "2026-09-17T00:00:00+00:00",
        "finished_at": "2026-09-17T01:00:00+00:00",
        "calculated_at": "2026-09-17T01:01:00+00:00",
        "task_type": "block_to_schematic",
        "agent": "codex",
        "model": "glm-4.5-air",
        "end_user": "100001",
        "metric_definition_version": "v1",
        "source_fingerprint": "fingerprint",
        "metrics": {"tool_success_rate": 100, "error_count": 0},
    }
    store.upsert_metrics(result)
    store.save_job({"job_id": "job-1", "status": "completed"})

    assert store.get_metrics("session-1")["model"] == "glm-4.5-air"
    assert store.statuses(["session-1"])["session-1"]["status"] == "completed"
    assert store.get_job("job-1") == {"job_id": "job-1", "status": "completed"}
    page = store.list_metrics(
        start_time=datetime(2026, 9, 17, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )
    assert page["total"] == 1
    assert page["items"][0]["session_id"] == "session-1"
