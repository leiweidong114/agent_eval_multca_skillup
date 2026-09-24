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
        manager._judge_executor.shutdown(wait=True)

    public_job = manager.get("metrics-test")
    assert public_job["status"] == "completed_with_errors"
    assert public_job["server_time"].tzinfo is not None
    assert store.saved_process[0] == "session-1"
    assert any(event["stage"] == "session_failed" for event in store.saved_process[1])


def test_metric_job_overlaps_independent_classification_and_session_judge(monkeypatch):
    import threading
    from datetime import datetime, timezone

    from app.metric_job_manager import MetricJobManager

    classification_started = threading.Event()
    metric_judge_started = threading.Event()
    conversation = {
        "root_session_id": "session-1",
        "timeline": [{"request_id": "r1", "messages": [{"role": "user", "content": "生成原理图"}]}],
    }

    def classify(*args, **kwargs):
        classification_started.set()
        assert metric_judge_started.wait(2), "session Judge did not start alongside classification"
        return {
            "status": "completed", "task_type": "other",
            "task_category": "other", "task_subtype": None,
        }

    def judge(*args, progress_callback, **kwargs):
        progress_callback("llm_judge_chunk_started", 1, 1, "开始分片")
        metric_judge_started.set()
        assert classification_started.wait(2), "classification did not start alongside session Judge"
        progress_callback("llm_judge_chunk_completed", 1, 1, "完成分片")
        return {"status": "completed", "task_type": "other", "suspected_fabrications": []}

    class FakeStore:
        def save_job(self, job):
            pass

        def query_endpoint(self):
            return "http://localhost/query"

        def latest_rationality_analysis(self, *args, **kwargs):
            return None, 0, None

        def latest_rationality_diagnostic(self):
            return {}

        def query_diagnostics(self):
            return []

        def build_process_trace(self, job, session_id):
            return {"job_id": job["job_id"], "session_id": session_id, "events": list(job["events"])}

        def build_metrics_record(self, result, *, process_trace=None):
            return {"uuid": "metric-1", "resultText": "{}"}

        def write_endpoint(self):
            return "http://localhost/insert"

        def upsert_metrics(self, result, *, record):
            return {"status": "ok"}

        def verify_metric_persisted(self, session_id, record_id):
            return {"verified": True}

        def write_diagnostics(self):
            return []

        def refresh_quality_aggregate(self):
            self.aggregate_refresh_count = getattr(self, "aggregate_refresh_count", 0) + 1
            return {"rates": {"框图规范检查总通过率": "50.00%"},
                    "source_session_count": 1, "updated_at": "2026-09-22T00:00:00+00:00"}

        def save_process_trace(self, job, session_id):
            self.saved_process = list(job["events"])

    monkeypatch.setattr("app.metric_job_manager.get_conversation", lambda *args, **kwargs: conversation)
    monkeypatch.setattr("app.metric_job_manager.classify_session_task", classify)
    monkeypatch.setattr("app.metric_job_manager.judge_session_metrics", judge)
    monkeypatch.setattr("app.metric_job_manager.resolve_config_secret", lambda *args, **kwargs: "2")
    manager = MetricJobManager()
    store = FakeStore()
    now = datetime.now(timezone.utc)
    manager._jobs["metrics-overlap"] = {
        "job_id": "metrics-overlap", "status": "queued", "session_ids": ["session-1"],
        "total": 1, "completed": 0, "failed": 0, "process_trace_failures": 0,
        "events": [], "event_seq": 0, "errors": [], "use_llm_judge": True,
        "classification_judge_enabled": True, "conversation_judge_enabled": True,
        "start_time": now, "end_time": now, "created_at": now,
    }
    try:
        manager._run("metrics-overlap", store)
    finally:
        manager._executor.shutdown(wait=True)
        manager._judge_executor.shutdown(wait=True)

    assert manager.get("metrics-overlap")["status"] == "completed"
    stages = [event["stage"] for event in store.saved_process]
    assert "llm_judge_chunk_started" in stages
    assert "task_classification_started" in stages
    assert "llm_judge_completed" in stages
    assert "quality_aggregate_updated" in stages
    assert store.aggregate_refresh_count == 1


def test_metric_job_disables_classification_and_conversation_judges_by_default(monkeypatch):
    from datetime import datetime, timezone

    from app.metric_job_manager import MetricJobManager

    class FakeStore:
        def save_job(self, job):
            pass

    monkeypatch.setattr("app.metric_job_manager.MetricsStore", FakeStore)
    monkeypatch.setattr("app.metric_job_manager.resolve_config_secret", lambda *args, **kwargs: "")
    manager = MetricJobManager()
    monkeypatch.setattr(manager._executor, "submit", lambda *args, **kwargs: None)
    now = datetime.now(timezone.utc)
    try:
        job = manager.submit(session_ids=["session-1"], user_id="100001", start_time=now,
                             end_time=now, use_llm_judge=True)
        assert job["use_llm_judge"] is True
        assert job["classification_judge_enabled"] is False
        assert job["conversation_judge_enabled"] is False
    finally:
        manager._executor.shutdown(wait=True)
        manager._judge_executor.shutdown(wait=True)


def test_metric_judge_parallelism_can_be_limited_to_one(monkeypatch):
    from app.metric_job_manager import MetricJobManager

    monkeypatch.setattr("app.metric_job_manager.resolve_config_secret", lambda *args, **kwargs: "1")
    manager = MetricJobManager()
    try:
        assert manager._judge_slots.acquire(blocking=False)
        assert not manager._judge_slots.acquire(blocking=False)
        manager._judge_slots.release()
    finally:
        manager._executor.shutdown(wait=True)
        manager._judge_executor.shutdown(wait=True)


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
