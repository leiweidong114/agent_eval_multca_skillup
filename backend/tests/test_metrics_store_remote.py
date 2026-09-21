import json
from datetime import datetime, timezone

from app.metrics_store import MetricsStore, SESSION_METRICS_CHECK_TYPE


class FakeClient:
    def __init__(self):
        self.records = []

    def insert_record(self, record, *, collection_name):
        saved = {**record, "_id": f"mongo-{len(self.records) + 1}"}
        self.records.append(saved)
        return {"status": "inserted", "record": saved, "collection": collection_name}

    def find_records(self, collection_name, identifiers):
        expected = set(identifiers)
        return [item for item in self.records if item.get("sessionId") in expected]

    def find_rationality_records(self, identifiers):
        return self.find_records("HDschematicRationalityCollection", identifiers)

    def iter_collection(self, collection_name):
        return list(self.records)


def test_remote_metrics_round_trip_without_local_database():
    client = FakeClient()
    store = MetricsStore.__new__(MetricsStore)
    store._client = client
    result = {
        "session_id": "session-1",
        "status": "completed",
        "started_at": "2026-09-17T00:00:00+00:00",
        "finished_at": "2026-09-17T01:00:00+00:00",
        "calculated_at": "2026-09-17T01:01:00+00:00",
        "task_type": "block_to_schematic",
        "task_category": "schematic_generation",
        "task_subtype": "block_to_schematic",
        "agent": "codex",
        "model": "glm-4.5-air",
        "end_user": "100001",
        "metric_definition_version": "v1",
        "source_fingerprint": "fingerprint",
        "metrics": {"tool_success_rate": 100, "error_count": 0},
    }

    store.upsert_metrics(result)

    assert client.records[0]["checkType"] == SESSION_METRICS_CHECK_TYPE
    assert json.loads(client.records[0]["resultText"])["model"] == "glm-4.5-air"
    assert store.get_metrics("session-1")["model"] == "glm-4.5-air"
    assert store.statuses(["session-1"])["session-1"]["status"] == "completed"
    assert store.session_ids_for_task_classification("schematic_generation") == {"session-1"}
    page = store.list_metrics(
        start_time=datetime(2026, 9, 17, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )
    assert page["total"] == 1
    assert page["items"][0]["session_id"] == "session-1"
    assert store.get_job("job-1") is None
