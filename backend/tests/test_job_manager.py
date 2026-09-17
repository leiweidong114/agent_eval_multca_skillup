from __future__ import annotations

import threading

from app.job_manager import EvaluationJobManager


def _manager_with_jobs(jobs: dict[str, dict]) -> EvaluationJobManager:
    manager = EvaluationJobManager.__new__(EvaluationJobManager)
    manager._lock = threading.RLock()
    manager._jobs = jobs
    manager._batches = {
        "batch-test": {
            "batch_id": "batch-test",
            "name": "ranking-test",
            "created_at": "2026-09-02T00:00:00",
            "job_ids": list(jobs),
        }
    }
    return manager


def test_failed_evaluations_are_not_ranked_even_when_they_have_scores():
    manager = _manager_with_jobs(
        {
            "failed": {
                "job_id": "failed",
                "status": "failed",
                "progress": 100,
                "agent": "claude",
                "model": "opencode-go/minimax-m2.7",
                "result": {
                    "scores": {"overall_score": 99},
                    "failure": {
                        "category": "gateway_quota_exhausted",
                        "title": "模型使用额度已达上限",
                        "detail": "OpenCode Go 的 5 小时模型使用额度已经用完。",
                    },
                },
            }
        }
    )

    batch = manager.get_batch("batch-test")

    assert batch is not None
    assert batch["status"] == "failed"
    assert batch["best"] is None
    assert "rank" not in batch["results"][0]
    assert batch["results"][0]["failure"]["category"] == "gateway_quota_exhausted"


def test_only_completed_evaluations_receive_a_rank():
    manager = _manager_with_jobs(
        {
            "failed": {
                "job_id": "failed",
                "status": "failed",
                "progress": 100,
                "agent": "claude",
                "result": {"scores": {"overall_score": 99}},
            },
            "completed": {
                "job_id": "completed",
                "status": "completed",
                "progress": 100,
                "agent": "codex",
                "result": {"scores": {"overall_score": 42}},
            },
        }
    )

    batch = manager.get_batch("batch-test")

    assert batch is not None
    assert batch["best"]["agent"] == "codex"
    assert batch["best"]["rank"] == 1
    assert "rank" not in batch["results"][0]
    assert batch["status"] == "partial_failed"


def test_transcript_messages_expose_model_and_tool_interactions():
    payload = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will inspect the file."},
                {"type": "tool_use", "name": "read_file", "input": {"path": "demo.txt"}},
            ],
        },
    }

    events = EvaluationJobManager._transcript_messages(payload)

    assert events[0][:2] == ("assistant", "I will inspect the file.")
    assert events[1][0] == "tool_call"
    assert events[1][2]["tool"] == "read_file"


def test_live_interaction_updates_one_turn_in_place():
    manager = _manager_with_jobs({"job": {"job_id": "job", "live_interactions": []}})
    manager._save = lambda _job: None
    request = {
        "request_id": "request-1",
        "start_time": "2026-09-17T09:00:00",
        "status": "started",
        "model_group": "glm-4.5-air",
        "proxy_server_request": {"messages": [{"role": "user", "content": "hi"}]},
    }
    response = {
        **request,
        "status": "success",
        "total_tokens": 12,
        "response": {"choices": [{"message": {"role": "assistant", "content": "hello"}}]},
    }

    manager._upsert_live_interaction("job", request)
    manager._upsert_live_interaction("job", response)

    rows = manager.get("job")["live_interactions"]
    assert len(rows) == 1
    assert rows[0]["request_id"] == "request-1"
    assert rows[0]["status"] == "success"
    assert rows[0]["turn_index"] == 1
    assert rows[0]["total_tokens"] == 12
