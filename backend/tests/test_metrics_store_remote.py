import json
from datetime import datetime, timezone

from app.metrics_store import MetricsStore, SESSION_METRICS_CHECK_TYPE, SESSION_PROCESS_CHECK_TYPE


class FakeClient:
    def __init__(self):
        self.records = []

    def insert_record(self, record, *, collection_name):
        saved = {**record, "_id": f"mongo-{len(self.records) + 1}"}
        self.records.append(saved)
        return {"status": "inserted", "record": saved, "collection": collection_name}

    def update_record(self, *, session_id, check_type, field, value, collection_name):
        matches = [item for item in self.records if item.get("sessionId") == session_id
                   and str(item.get("checkType") or "").strip() == check_type]
        if not matches:
            raise RuntimeError("source record not found")
        matches[-1][field] = value
        return {"status": "updated", "matchedCount": 1, "field": field}

    def upsert_aggregate_metrics(self, *, value, collection_name):
        matches = [item for item in self.records if item.get("sessionId") == "汇总结果"
                   and item.get("checkType") == "agent_eval_quality_aggregate"]
        if len(matches) > 1:
            raise RuntimeError("duplicate aggregate documents")
        if not matches:
            self.insert_record({"sessionId": "汇总结果", "checkType": "agent_eval_quality_aggregate",
                                "uuid": "aggregate-uuid", "createTime": value["updated_at"]},
                               collection_name=collection_name)
            matches = [self.records[-1]]
        matches[0]["agentEvalMetrics"] = dict(value)
        matches[0]["resultText"] = json.dumps(value, ensure_ascii=False)
        matches[0]["createTime"] = value["updated_at"]
        return {"status": "updated", "matchedCount": 1}

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
    verification = store.verify_metric_persisted("session-1", client.records[0]["uuid"])

    assert client.records[0]["checkType"] == SESSION_METRICS_CHECK_TYPE
    assert verification["verified"] is True
    assert json.loads(client.records[0]["resultText"])["model"] == "glm-4.5-air"
    assert store.get_metrics("session-1")["model"] == "glm-4.5-air"
    assert store.statuses(["session-1"])["session-1"]["status"] == "completed"
    assert store.all_statuses()["session-1"]["task_type"] == "block_to_schematic"
    assert store.session_ids_for_task_classification("schematic_generation") == {"session-1"}
    page = store.list_metrics(
        start_time=datetime(2026, 9, 17, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )
    assert page["total"] == 1
    assert page["items"][0]["session_id"] == "session-1"
    assert store.get_job("job-1") is None


def test_write_read_verification_rejects_missing_uuid():
    client = FakeClient()
    store = MetricsStore.__new__(MetricsStore)
    store._client = client

    verification = store.verify_metric_persisted("session-missing", "uuid-not-present")

    assert verification["verified"] is False
    assert "没有找到本次 UUID" in verification["reason"]


def test_completed_process_trace_round_trip_and_rationality_exclusion():
    client = FakeClient()
    store = MetricsStore.__new__(MetricsStore)
    store._client = client
    job = {
        "job_id": "metrics-test",
        "user_id": "100001",
        "use_llm_judge": True,
        "created_at": datetime(2026, 9, 21, tzinfo=timezone.utc),
        "events": [
            {"sequence": 1, "session_id": "session-1", "stage": "session_loaded", "message": "读取成功"},
            {"sequence": 2, "session_id": "session-1", "stage": "session_completed", "message": "计算完成"},
            {"sequence": 3, "session_id": "another", "stage": "session_failed", "message": "其他会话"},
        ],
    }

    saved = store.save_process_trace(job, "session-1")
    loaded = store.get_process_trace("session-1")

    assert saved["status"] == "completed"
    assert client.records[0]["checkType"] == SESSION_PROCESS_CHECK_TYPE
    assert loaded["job_id"] == "metrics-test"
    assert [event["stage"] for event in loaded["events"]] == ["session_loaded", "session_completed"]
    assert loaded["mongo_record_id"] == "mongo-1"
    record, count, _ = store.latest_rationality_analysis("session-1")
    assert record is None
    assert count == 0


def test_quality_metrics_and_process_are_new_documents_without_mutating_source():
    client = FakeClient()
    source = {"_id": "mongo-source", "sessionId": "session-1",
              "checkType": "hscope_block_corpus_check  ", "status": "pending", "resultText": "语料库覆盖率: 75.0%"}
    client.records.append(source)
    store = MetricsStore.__new__(MetricsStore)
    store._client = client
    result = {"session_id": "session-1", "status": "completed", "end_user": "100001",
              "task_type": "other", "metric_definition_version": "v1", "metrics": {},
              "schematic_rationality": {"check_type": "hscope_block_corpus_check"},
              "quality_summary": {"session_id": "session-1", "rates": {"语料覆盖率": "75.00%"}}}
    record = store.build_metrics_record(result)
    response = store.upsert_metrics(result, record=record)
    assert response["status"] == "inserted"
    assert len(client.records) == 2
    assert "agentEvalMetrics" not in source
    assert client.records[1]["agentEvalMetrics"]["语料库覆盖"]["语料覆盖率"] == "75.00%"
    assert set(client.records[1]["agentEvalMetrics"]) == {"框图规范检查", "语料库覆盖", "信号接口列表检查", "天枢DRC审查"}
    assert store.verify_metric_persisted("session-1", record["uuid"])["verified"] is True
    assert store.get_metrics("session-1")["schematic_rationality"]["check_type"] == "hscope_block_corpus_check"
    store.save_process_trace({"job_id": "job-1", "events": [
        {"session_id": "session-1", "stage": "session_completed"}]}, "session-1")
    assert len(client.records) == 3
    assert store.get_process_trace("session-1")["job_id"] == "job-1"
