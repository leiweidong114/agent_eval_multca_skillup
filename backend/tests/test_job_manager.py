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
                "result": {"scores": {"overall_score": 99}},
            }
        }
    )

    batch = manager.get_batch("batch-test")

    assert batch is not None
    assert batch["status"] == "completed"
    assert batch["best"] is None
    assert "rank" not in batch["results"][0]


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
