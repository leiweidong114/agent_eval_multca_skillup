import json

from app import schematic_rationality_judge as subject
from app.metrics_store import MetricsStore


def test_source_numeric_metrics_override_judge_and_are_clamped(monkeypatch):
    monkeypatch.setattr(
        subject,
        "run_json_judge",
        lambda **kwargs: {
            "model": "judge-model",
            "judge_interaction_id": "judge-1",
            "usage": {"total_tokens": 42},
            "result": {
                "quality_level": "good",
                "metrics": {"overall_score": 12, "signal_integrity": 88},
                "summary": "质量总体良好。",
                "issues": [{"severity": "low", "category": "ERC", "message": "存在告警"}],
            },
        },
    )
    record = {
        "sessionId": "run-1",
        "resultText": json.dumps({
            "overall_score": 120,
            "erc_error_count": -3,
        }),
    }

    result = subject.judge_rationality_result(record, employee_no="001")

    assert result["status"] == "completed"
    assert result["metrics"]["overall_score"] == 100
    assert result["metrics"]["erc_error_count"] == 0
    assert result["metrics"]["signal_integrity"] == 88
    assert result["summary"] == "质量总体良好。"


def test_text_source_uses_only_judge_extracted_metrics(monkeypatch):
    monkeypatch.setattr(
        subject,
        "run_json_judge",
        lambda **kwargs: {
            "result": {
                "quality_level": "fair",
                "metrics": {"warning_count": 2, "unknown_metric": 99},
                "summary": "有两项告警。",
                "issues": [],
            }
        },
    )

    result = subject.judge_rationality_result({"sessionId": "s", "resultText": "检测到两项告警"})

    assert result["source_format"] == "text"
    assert result["metrics"] == {"warning_count": 2}


class FakeCollection:
    def __init__(self, value):
        self.value = value
        self.count_query = None
        self.find_query = None

    def count_documents(self, query):
        self.count_query = query
        return 1

    def find_one(self, query, sort=None):
        self.find_query = query
        return dict(self.value)


def test_store_can_join_rationality_record_by_evaluation_run_id():
    store = MetricsStore.__new__(MetricsStore)
    collection = FakeCollection({
        "_id": "mongo-1",
        "sessionId": "run-42",
        "status": "completed",
        "resultText": "{}",
    })
    store._rationality = collection

    record, count, matched_by = store.latest_rationality_analysis(
        "root-session", correlation_ids=["run-42"]
    )

    assert count == 1
    assert record["_id"] == "mongo-1"
    assert matched_by == "evaluation_run_id"
    assert collection.count_query == {"sessionId": {"$in": ["root-session", "run-42"]}}
