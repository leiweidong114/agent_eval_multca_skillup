from app.session_metrics import calculate_rule_metrics


def test_failed_metric_job_still_persists_its_process(monkeypatch):
    from datetime import datetime, timezone

    from app.metric_job_manager import MetricJobManager

    def missing_session(*args, **kwargs):
        raise ValueError("会话读取失败")

    class FakeStore:
        def __init__(self):
            self.saved_process = None

        def save_job(self, job):
            pass

        def save_process_trace(self, job, session_id):
            self.saved_process = (session_id, list(job["events"]))

    monkeypatch.setattr("app.metric_job_manager.get_conversation", missing_session)
    manager = MetricJobManager()
    store = FakeStore()
    now = datetime.now(timezone.utc)
    manager._jobs["metrics-test"] = {
        "job_id": "metrics-test", "status": "queued", "session_ids": ["session-1"],
        "total": 1, "completed": 0, "failed": 0, "process_trace_failures": 0,
        "events": [], "event_seq": 0, "errors": [],
        "start_time": now, "end_time": now, "created_at": now,
    }
    try:
        manager._run("metrics-test", store)
    finally:
        manager._executor.shutdown(wait=True)

    public_job = manager.get("metrics-test")
    assert public_job["status"] == "completed_with_errors"
    assert public_job["server_time"].tzinfo is not None
    assert store.saved_process[0] == "session-1"
    assert any(event["stage"] == "session_failed" for event in store.saved_process[1])


def test_rule_metrics_correlate_tools_scripts_retries_and_skill_steps():
    conversation = {
        "root_session_id": "session-1",
        "agent": "codex",
        "models": ["glm-4.5-air"],
        "timeline": [
            {
                "request_id": "r1",
                "status": "success",
                "response": {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "shell", "arguments": "python validate_sheets.py sheets.json"}}]}}]},
            },
            {
                "request_id": "r2",
                "status": "success",
                "messages": [{"role": "tool", "tool_call_id": "c1", "content": "error: invalid sheet"}],
                "response": {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "c2", "function": {"name": "shell", "arguments": "python validate_sheets.py sheets.json"}}]}}]},
            },
            {
                "request_id": "r3",
                "status": "success",
                "messages": [{"role": "tool", "tool_call_id": "c2", "content": "validation complete"}],
                "response": {"choices": [{"message": {"role": "assistant", "content": "schematic-layout-codegen then schematic-web-apply apply.py"}}]},
            },
        ],
    }

    result = calculate_rule_metrics(conversation)

    assert result["task_type"] == "block_to_schematic"
    assert result["metrics"]["tool_success_rate"] == 50
    assert result["metrics"]["script_success_rate"] == 50
    assert result["metrics"]["retry_attempt_count"] == 1
    assert result["metrics"]["recovered_error_count"] == 1
    assert result["metrics"]["skill_completeness"] == 100
    assert result["metrics"]["confirmed_fabrication_count"] is None


def test_rule_metrics_do_not_report_missing_evidence_as_perfect_success():
    result = calculate_rule_metrics({"root_session_id": "empty", "timeline": []})
    assert result["metrics"]["tool_success_rate"] is None
    assert result["metrics"]["script_success_rate"] is None
    assert result["metrics"]["skill_completeness"] is None
    assert result["fabrication_status"] == "requires_llm_and_artifact_evidence"
