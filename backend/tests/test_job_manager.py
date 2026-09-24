from __future__ import annotations

import threading
import time

import app.job_manager as job_manager_module
from app.job_manager import EvaluationJobManager


def _manager_with_jobs(jobs: dict[str, dict]) -> EvaluationJobManager:
    manager = EvaluationJobManager.__new__(EvaluationJobManager)
    manager._lock = threading.RLock()
    manager._jobs = jobs
    manager._futures = {}
    manager._pending = {}
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


def test_sparse_live_snapshot_does_not_erase_saved_model_output():
    manager = _manager_with_jobs({"job": {"job_id": "job", "live_interactions": []}})
    manager._save = lambda _job: None
    complete = {
        "request_id": "request-1",
        "status": "success",
        "total_tokens": 12,
        "messages": [{"role": "user", "content": "hi"}],
        "response": {
            "choices": [{"message": {"role": "assistant", "content": "hello"}}]
        },
    }
    sparse = {
        "request_id": "request-1",
        "status": "success",
        "total_tokens": 12,
        "response": None,
    }

    manager._upsert_live_interaction("job", complete)
    manager._upsert_live_interaction("job", sparse)

    row = manager.get("job")["live_interactions"][0]
    assert row["response"] == complete["response"]
    assert row["messages"] == complete["messages"]


def test_batch_runs_in_parallel_and_one_failure_does_not_cancel_others(
    monkeypatch, tmp_path
):
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def fake_run_evaluation(**kwargs):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep(0.12)
            if kwargs["agent"] == "broken-agent":
                raise RuntimeError("intentional agent failure")
            return {
                "status": "completed",
                "provider_model": kwargs.get("model"),
                "scores": {"overall_score": 0.8},
                "scoring": {"valid_for_ranking": True},
            }
        finally:
            with lock:
                active -= 1

    monkeypatch.setenv("AGENT_EVAL_WORKERS", "3")
    monkeypatch.setattr(job_manager_module, "runs_root", lambda: tmp_path / "runs")
    monkeypatch.setattr(job_manager_module, "readable_runs_roots", lambda: (tmp_path / "runs",))
    monkeypatch.setattr(job_manager_module, "BACKEND_ROOT", tmp_path)
    monkeypatch.setattr(job_manager_module, "run_evaluation", fake_run_evaluation)
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    manager = EvaluationJobManager()
    try:
        batch = manager.submit_batch(
            [
                {"agent": "agent-a", "model": "test-model", "user_id": "tester"},
                {"agent": "broken-agent", "model": "test-model", "user_id": "tester"},
                {"agent": "agent-c", "model": "test-model", "user_id": "tester"},
            ],
            skill_dir,
            name="parallel fault isolation",
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            batch = manager.get_batch(batch["batch_id"])
            if batch and batch["completed_jobs"] == 3:
                break
            time.sleep(0.02)

        assert batch is not None
        assert maximum_active == 3
        assert batch["status"] == "partial_failed"
        assert sorted(row["status"] for row in batch["results"]) == [
            "completed", "completed", "failed"
        ]
    finally:
        manager._executor.shutdown(wait=True)


def test_cancel_batch_marks_each_active_job_and_batch_as_cancelling():
    manager = _manager_with_jobs(
        {
            "running": {"job_id": "running", "status": "running", "progress": 20},
            "queued": {"job_id": "queued", "status": "queued", "progress": 0},
            "completed": {"job_id": "completed", "status": "completed", "progress": 100},
        }
    )
    manager._cancel = {
        "running": threading.Event(),
        "queued": threading.Event(),
        "completed": threading.Event(),
    }
    manager._save = lambda _job: None
    manager._save_batch = lambda _batch: None

    batch = manager.cancel_batch("batch-test")

    assert batch is not None
    assert batch["status"] == "cancelling"
    assert manager.get("running")["status"] == "cancelling"
    assert manager.get("queued")["status"] == "cancelling"
    assert manager.get("completed")["status"] == "completed"
    assert manager._cancel["running"].is_set()
    assert manager._cancel["queued"].is_set()

    manager._jobs["running"]["status"] = "cancelled"
    manager._jobs["queued"]["status"] = "cancelled"
    finished = manager.get_batch("batch-test")
    assert finished is not None
    assert finished["status"] == "cancelled"


def test_prioritized_job_runs_before_jobs_already_waiting_in_queue(monkeypatch, tmp_path):
    started: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()

    def fake_run_evaluation(**kwargs):
        started.append(kwargs["agent"])
        if kwargs["agent"] == "first":
            first_started.set()
            assert release_first.wait(2)
        return {"status": "completed", "scores": {"overall_score": 1}}

    monkeypatch.setenv("AGENT_EVAL_WORKERS", "1")
    monkeypatch.setattr(job_manager_module, "runs_root", lambda: tmp_path / "runs")
    monkeypatch.setattr(job_manager_module, "readable_runs_roots", lambda: (tmp_path / "runs",))
    monkeypatch.setattr(job_manager_module, "BACKEND_ROOT", tmp_path)
    monkeypatch.setattr(job_manager_module, "run_evaluation", fake_run_evaluation)
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    manager = EvaluationJobManager()
    try:
        first = manager.submit({"agent": "first"}, skill_dir)
        assert first_started.wait(1)
        normal = manager.submit({"agent": "normal"}, skill_dir)
        priority = manager.submit({"agent": "priority"}, skill_dir)

        promoted = manager.prioritize(priority["job_id"])
        assert promoted is not None
        assert promoted["prioritized"] is True
        release_first.set()

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if all(
                manager.get(job["job_id"])["status"] == "completed"
                for job in (first, normal, priority)
            ):
                break
            time.sleep(0.02)
        assert started == ["first", "priority", "normal"]
    finally:
        release_first.set()
        manager._executor.shutdown(wait=True)


def test_new_jobs_use_new_results_root_without_moving_running_jobs(monkeypatch, tmp_path):
    roots = [tmp_path / "short-a", tmp_path / "short-b"]
    current = [roots[0]]
    outputs = {}

    def fake_run_evaluation(**kwargs):
        outputs[kwargs["run_id"]] = kwargs["output_dir"]
        return {"status": "completed", "scores": {"overall_score": 1}}

    monkeypatch.setattr(job_manager_module, "runs_root", lambda: current[0])
    monkeypatch.setattr(job_manager_module, "readable_runs_roots", lambda: tuple(roots))
    monkeypatch.setattr(job_manager_module, "run_evaluation", fake_run_evaluation)
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    manager = EvaluationJobManager()
    try:
        first = manager.submit({"agent": "codex"}, skill_dir)
        current[0] = roots[1]
        second = manager.submit({"agent": "codex"}, skill_dir)
    finally:
        manager._executor.shutdown(wait=True)
    assert outputs[first["job_id"]] == str(roots[0])
    assert outputs[second["job_id"]] == str(roots[1])
    assert (roots[0] / "_jobs" / f"{first['job_id']}.json").is_file()
    assert (roots[1] / "_jobs" / f"{second['job_id']}.json").is_file()
