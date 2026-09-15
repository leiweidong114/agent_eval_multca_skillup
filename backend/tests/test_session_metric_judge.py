from __future__ import annotations

from app.session_metric_judge import _chunks, judge_session_metrics


def test_chunks_bound_large_rows() -> None:
    rows = [{"request_id": f"r{i}", "messages": [{"content": "x" * 30000}]} for i in range(4)]
    values = _chunks(rows, max_chars=20000)
    assert len(values) == 4
    assert all(len(chunk) == 1 for chunk in values)


def test_judge_rejects_unknown_evidence_ids(monkeypatch) -> None:
    def fake_judge(**kwargs):
        return {
            "model": "judge-model",
            "usage": {"total_tokens": 12},
            "result": {
                "task_type": "other",
                "suspected_fabrications": [
                    {"event_id": "valid", "request_id": "r1", "confidence": 0.8},
                    {"event_id": "fake", "request_id": "not-in-source", "confidence": 1.0},
                ],
            },
        }

    monkeypatch.setattr("app.session_metric_judge.run_json_judge", fake_judge)
    result = judge_session_metrics({"timeline": [{"request_id": "r1", "messages": []}]})
    assert result["status"] == "completed"
    assert [item["event_id"] for item in result["suspected_fabrications"]] == ["valid"]
