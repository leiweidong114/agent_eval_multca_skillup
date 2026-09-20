from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_eval.database import (
    DatabaseConfig,
    DatabaseConfigurationError,
    _database_retry,
    _load_conversation_rows,
    _sanitize,
    build_conversation_groups,
    conversation_time_window,
    enrich_interaction_rows,
    resolve_database_config,
    summarize_interaction_rows,
    summarize_model_interactions,
    verify_requested_model,
    fetch_model_interactions,
    search_conversations,
)


def test_conversation_time_window_defaults_to_latest_24_hours():
    before = datetime.now(timezone.utc)
    start, end = conversation_time_window()
    after = datetime.now(timezone.utc)

    assert before <= end <= after
    assert end - start == timedelta(hours=24)


def test_conversation_time_window_rejects_reverse_range():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="start_time"):
        conversation_time_window(now, now - timedelta(seconds=1))


def test_conversation_groups_attach_subagents_to_root_session():
    rows = [
        {
            "request_id": "main-1", "session_id": "root", "model": "glm-4.5-air",
            "start_time": "2026-09-09T10:00:00+08:00", "end_time": "2026-09-09T10:00:01+08:00",
            "total_tokens": 10, "metadata": {"agent_eval_task_id": "task-1", "agent_eval_agent": "justdo"},
        },
        {
            "request_id": "child-1", "session_id": "child", "model": "glm-4.5-air",
            "start_time": "2026-09-09T10:00:02+08:00", "end_time": "2026-09-09T10:00:03+08:00",
            "total_tokens": 20, "proxy_server_request": {"metadata": {"parent_session_id": "root", "agent_eval_task_id": "task-1"}},
        },
        {
            "request_id": "grandchild-1", "session_id": "grandchild", "model": "glm-4.5-air",
            "start_time": "2026-09-09T10:00:04+08:00", "end_time": "2026-09-09T10:00:05+08:00",
            "total_tokens": 30, "metadata": {"parent_session_id": "child", "agent_eval_task_id": "task-1"},
        },
    ]

    conversations = build_conversation_groups(rows)

    assert len(conversations) == 1
    conversation = conversations[0]
    assert conversation["root_session_id"] == "root"
    assert conversation["interaction_count"] == 3
    assert conversation["subagent_count"] == 2
    assert conversation["total_tokens"] == 60
    assert [node["depth"] for node in conversation["sessions"]] == [0, 1, 2]
    assert conversation["source_kind"] == "evaluation"


def test_conversation_groups_do_not_merge_missing_session_ids():
    conversations = build_conversation_groups([
        {"request_id": "one", "model": "m"},
        {"request_id": "two", "model": "m"},
    ])

    assert {item["root_session_id"] for item in conversations} == {
        "unattributed:one", "unattributed:two",
    }


def test_conversation_summary_uses_attribution_metadata_fallbacks():
    conversations = build_conversation_groups([{
        "request_id": "metadata-only",
        "metadata": {
            "session_id": "session-1",
            "agent_eval_user_id": "E10001",
            "agent_eval_agent": "justdo",
            "agent_eval_model": "glm-4.5-air",
        },
    }])

    assert conversations[0]["user_id"] == "E10001"
    assert conversations[0]["agent"] == "justdo"
    assert conversations[0]["models"] == ["glm-4.5-air"]


def test_targeted_conversation_loader_collects_multiple_subagents():
    now = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    root_row = {
        "request_id": "root-1", "session_id": "root", "session_key": "root-key",
        "parent_session_id": None, "start_time": now, "end_time": now + timedelta(seconds=1),
    }
    child_rows = [
        {
            "request_id": "child-a-1", "session_id": "child-a", "session_key": "child-a-key",
            "parent_session_id": "root", "start_time": now + timedelta(seconds=2),
            "end_time": now + timedelta(seconds=3),
        },
        {
            "request_id": "child-b-1", "session_id": "child-b", "session_key": "child-b-key",
            "parent_session_id": "root", "start_time": now + timedelta(seconds=4),
            "end_time": now + timedelta(seconds=5),
        },
    ]

    class Cursor:
        rows = []
        edge_calls = 0

        def execute(self, query, params):
            if "select distinct" in query:
                self.edge_calls += 1
                self.rows = (
                    [
                        {"session_id": "child-a", "session_key": "child-a-key"},
                        {"session_id": "child-b", "session_key": "child-b-key"},
                    ]
                    if self.edge_calls == 1 else []
                )
                return
            requested = set(params[-1]) if isinstance(params[-1], list) else set()
            if requested == {"root"}:
                self.rows = [root_row]
            elif requested == {"child-a", "child-b"}:
                self.rows = child_rows
            else:
                self.rows = []

        def fetchall(self):
            return self.rows

    root, rows = _load_conversation_rows(
        Cursor(),
        requested_session_id="root",
        user_clause="true",
        user_parameters=[],
        window_start=now - timedelta(hours=1),
        window_end=now + timedelta(hours=1),
        include_content=False,
        exclude_judge=True,
    )

    assert root == "root"
    assert {row["session_id"] for row in rows} == {"root", "child-a", "child-b"}


def test_exact_session_search_uses_targeted_family_lookup(monkeypatch, tmp_path):
    config = DatabaseConfig(
        enabled=True, host="db", port=5432, name="litellm", user="reader", password="x",
        sslmode="prefer", connect_timeout_seconds=1, trace_enabled=True,
        include_content=True, lookaround_seconds=0, limit=500, retention_days=30,
        max_content_chars=20000,
    )
    now = datetime.now(timezone.utc)
    conversation = {
        "root_session_id": "root", "session_id": "root", "source_kind": "evaluation",
        "interaction_count": 3, "models": ["glm-4.5-air"], "timeline": [
            {"session_id": "root"}, {"session_id": "child-a"}, {"session_id": "child-b"},
        ],
    }
    monkeypatch.setattr("agent_eval.database.resolve_database_config", lambda _root: config)
    monkeypatch.setattr("agent_eval.database.get_conversation", lambda *_args, **_kwargs: dict(conversation))
    result = search_conversations(
        tmp_path, session_id="child-a", start_time=now - timedelta(hours=1), end_time=now,
    )

    assert result["query_strategy"] == "indexed_session_family"
    assert result["scan_truncated"] is False
    assert result["conversations"][0]["root_session_id"] == "root"
    assert result["conversations"][0]["interaction_count"] == 3


def test_end_user_search_is_filtered_in_database_without_scan_cap(monkeypatch, tmp_path):
    config = DatabaseConfig(
        enabled=True, host="db", port=5432, name="litellm", user="reader", password="x",
        sslmode="prefer", connect_timeout_seconds=1, trace_enabled=True,
        include_content=True, lookaround_seconds=0, limit=500, retention_days=30,
        max_content_chars=20000,
    )
    now = datetime.now(timezone.utc)
    observed = {}
    rows = [{
        "request_id": "request-1", "call_type": "acompletion", "user_id": None,
        "end_user": "100086", "start_time": now - timedelta(seconds=2),
        "end_time": now, "model": "glm-4.5-air", "model_group": "glm-4.5-air",
        "custom_llm_provider": "openai", "session_id": "session-x",
        "parent_session_id": None, "session_key": None, "parent_session_key": None,
        "spawned_by": None, "evaluation_task_id": None, "evaluation_run_id": None,
        "top_level_agent": "justdo", "requested_model": "glm-4.5-air",
        "request_purpose": "agent", "key_alias": None, "status": "success",
        "agent_id": None, "request_duration_ms": 2000, "prompt_tokens": 10,
        "completion_tokens": 5, "total_tokens": 15, "spend": 0,
    }]

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def execute(self, query, params):
            observed["query"] = query
            observed["params"] = params
        def fetchall(self): return rows

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def cursor(self): return Cursor()

    class Psycopg:
        @staticmethod
        def connect(**_kwargs): return Connection()

    monkeypatch.setattr("agent_eval.database.resolve_database_config", lambda _root: config)
    monkeypatch.setattr("agent_eval.database._driver", lambda: (Psycopg, object()))
    result = search_conversations(
        tmp_path, end_user="100086",
        start_time=now - timedelta(days=30), end_time=now + timedelta(seconds=1),
    )

    assert "end_user = %s" in observed["query"]
    assert "limit %s" not in observed["query"].lower()
    assert observed["params"][0] == "100086"
    assert result["query_strategy"] == "indexed_end_user_overview"
    assert result["scan_truncated"] is False
    assert result["conversations"][0]["root_session_id"] == "session-x"


def test_fetch_interactions_paginates_past_500(monkeypatch, tmp_path):
    config = DatabaseConfig(
        enabled=True, host="db", port=5432, name="litellm", user="reader", password="x",
        sslmode="prefer", connect_timeout_seconds=1, trace_enabled=True,
        include_content=True, lookaround_seconds=0, limit=500, retention_days=30,
        max_content_chars=20000,
    )
    source_rows = [
        {"request_id": f"r{i}", "status": "success", "session_id": "main"}
        for i in range(1201)
    ]
    offsets = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def execute(self, _query, params):
            self.limit, self.offset = params[-2:]
            offsets.append(self.offset)
        def fetchall(self):
            return source_rows[self.offset:self.offset + self.limit]

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def cursor(self): return Cursor()

    class Psycopg:
        @staticmethod
        def connect(**_kwargs): return Connection()

    monkeypatch.setattr("agent_eval.database.resolve_database_config", lambda _root: config)
    monkeypatch.setattr("agent_eval.database._driver", lambda: (Psycopg, object()))
    rows = fetch_model_interactions(
        tmp_path, started_at=datetime(2026, 1, 1), finished_at=datetime(2026, 1, 1),
        model="m", key_alias="agent-eval-run", task_id="task", user_id="E1", agent="justdo",
    )
    assert len(rows) == 1201
    assert offsets == [0, 500, 1000]
    assert rows[-1]["evaluation_task_id"] == "task"


def _write_database_config(root: Path) -> None:
    config = root / "config"
    config.mkdir()
    (config / "database.yaml").write_text(
        """\
database:
  enabled: true
  host: db.example
  port: 5432
  name: litellm
  user: reader
  password_env: TEST_DB_PASSWORD
  trace:
    enabled: true
    include_content: true
""",
        encoding="utf-8",
    )


def test_database_config_uses_environment_secret(tmp_path):
    _write_database_config(tmp_path)
    config = resolve_database_config(tmp_path, environ={"TEST_DB_PASSWORD": "secret"})
    assert config.host == "db.example"
    assert config.password == "secret"
    assert config.trace_enabled is True
    assert config.include_content is True
    assert config.retention_days == 30


def test_database_config_rejects_missing_password(tmp_path):
    _write_database_config(tmp_path)
    with pytest.raises(DatabaseConfigurationError, match="TEST_DB_PASSWORD"):
        resolve_database_config(tmp_path, environ={})


def test_database_config_uses_repository_root_env_url(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    _write_database_config(backend)
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://env_reader:env_secret@db.internal:5544/eval_db\n",
        encoding="utf-8",
    )

    config = resolve_database_config(backend, environ={})

    assert config.enabled is True
    assert config.host == "db.internal"
    assert config.port == 5544
    assert config.name == "eval_db"
    assert config.user == "env_reader"
    assert config.password == "env_secret"


def test_database_config_uses_repository_root_env_fields(tmp_path):
    _write_database_config(tmp_path)
    (tmp_path / ".env").write_text(
        """\
DATABASE_HOST=db.fields
DATABASE_PORT=6432
DATABASE_NAME=field_db
DATABASE_USER=field_reader
DATABASE_PASSWORD=field_secret
DATABASE_ENABLED=true
""",
        encoding="utf-8",
    )

    config = resolve_database_config(tmp_path, environ={})

    assert (config.host, config.port, config.name) == ("db.fields", 6432, "field_db")
    assert (config.user, config.password) == ("field_reader", "field_secret")


def test_model_interaction_summary_is_deterministic():
    summary = summarize_model_interactions(
        [
            {
                "status": "success",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "spend": 0.1,
                "request_duration_ms": 100,
                "start_time": datetime(2026, 1, 1),
            },
            {
                "status": "failure",
                "prompt_tokens": 3,
                "completion_tokens": 0,
                "total_tokens": 3,
                "spend": 0,
                "request_duration_ms": 300,
            },
        ]
    )
    assert summary["model_call_count"] == 2
    assert summary["model_call_success_rate"] == 50
    assert summary["total_tokens"] == 18
    assert summary["average_request_duration_ms"] == 200
    assert summarize_model_interactions([], exact=True)["correlation"] == "run_scoped_virtual_key"


def test_requested_model_verification_requires_exact_successful_match():
    rows = [{
        "request_id": "req-1", "status": "success",
        "model": "anthropic/minimax-m2.7", "model_group": "opencode-go/minimax-m2.7",
        "model_id": "deployment-1",
    }]
    verified = verify_requested_model(
        rows, expected_model="opencode-go/minimax-m2.7", exact=True
    )
    assert verified["verified"] is True
    assert verified["successful_matching_calls"] == 1
    weak = verify_requested_model(
        rows, expected_model="opencode-go/minimax-m2.7", exact=False
    )
    assert weak["verified"] is False
    assert weak["model_matched"] is True
    assert weak["exact_agent_attribution"] is False
    assert weak["status"] == "matched_unattributed"
    assert weak["reason"] == "exact_run_correlation_unavailable"
    assert weak["warning"]


def test_requested_model_verification_accepts_gateway_model_alias():
    rows = [{
        "request_id": "req-alias", "status": "success",
        "model": "glm-4.7-anthropic", "model_group": "",
        "model_id": "deployment-glm",
    }]
    result = verify_requested_model(
        rows,
        expected_model="glm-4.7",
        accepted_model_groups=["glm-4.7-anthropic", "sonnet"],
        exact=True,
    )
    assert result["verified"] is True
    assert result["mismatches"] == []


def test_requested_model_verification_reports_mismatch():
    result = verify_requested_model(
        [{"request_id": "req-2", "status": "success", "model": "openai/gpt-4.1", "model_group": "gpt-4.1"}],
        expected_model="opencode-go/minimax-m2.7",
        exact=True,
    )
    assert result["verified"] is False
    assert result["reason"] == "requested_model_mismatch"
    assert result["mismatches"][0]["request_id"] == "req-2"


def test_failed_request_to_another_model_is_diagnostic_not_a_successful_model_mismatch():
    result = verify_requested_model(
        [
            {"request_id": "ok", "status": "success", "model": "glm-4.5-air", "model_group": "glm-4.5-air"},
            {"request_id": "failed", "status": "failure", "model": "gpt-5.5", "model_group": ""},
        ],
        expected_model="glm-4.5-air",
        exact=True,
    )

    assert result["verified"] is True
    assert result["mismatches"] == []
    assert result["failed_mismatched_attempts"][0]["request_id"] == "failed"


def test_database_retry_recovers_from_transient_connection_failure(monkeypatch):
    attempts = 0

    class OperationalError(RuntimeError):
        pass

    def operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OperationalError("connection reset by peer")
        return "ok"

    monkeypatch.setattr("agent_eval.database.time.sleep", lambda _: None)
    assert _database_retry(operation) == "ok"
    assert attempts == 3


def test_interaction_content_redacts_nested_credentials():
    result = _sanitize(
        {"metadata": {"user_api_key": "secret", "user_api_key_alias": "eval-run"}},
        max_chars=100,
    )
    assert result["metadata"]["user_api_key"] == "[REDACTED]"
    assert result["metadata"]["user_api_key_alias"] == "eval-run"


def test_interaction_metrics_count_new_tool_and_subagent_calls():
    rows = [{
        "start_time": "2026-09-09T10:00:00+08:00",
        "end_time": "2026-09-09T10:00:03+08:00",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "response": {"choices": [{"message": {"tool_calls": [
            {"function": {"name": "read"}},
            {"function": {"name": "sessions_spawn"}},
        ]}}]},
    }]

    enrich_interaction_rows(rows)
    assert rows[0]["tool_call_count"] == 2
    assert rows[0]["subagent_start_count"] == 1
    assert rows[0]["turn_index"] == 1
    summary = summarize_interaction_rows(rows)
    assert summary["duration_ms"] == 3000
    assert summary["total_tokens"] == 15
    assert summary["tool_call_count"] == 2


def test_interaction_metrics_recognize_all_certified_subagent_tool_shapes():
    rows = [{"response": {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "Agent", "arguments": "{}"}},
        {"function": {"name": "task", "arguments": "{}"}},
        {"function": {"name": "multi_agent_v1__spawn_agent", "arguments": "{}"}},
        {"function": {"name": "exec", "arguments": '{"command":"openclaw agent exec --json hi"}'}},
        {"function": {"name": "exec", "arguments": '{"command":"python ordinary.py"}'}},
    ]}}]}}]

    enrich_interaction_rows(rows)

    assert rows[0]["tool_call_count"] == 5
    assert rows[0]["subagent_start_count"] == 4


def test_interaction_rows_distinguish_main_agent_and_named_subagent():
    rows = [
        {
            "proxy_server_request": {
                "messages": [{"role": "user", "content": "普通主任务"}]
            },
            "response": {},
        },
        {
            "proxy_server_request": {
                "messages": [{
                    "role": "user",
                    "content": "[Subagent Context] child\n\n[Subagent Task]\n处理 slice_id=POWER",
                }]
            },
            "response": {},
        },
    ]

    enrich_interaction_rows(rows)

    assert rows[0]["interaction_scope"] == "main_agent"
    assert rows[0]["subagent_name"] is None
    assert rows[1]["interaction_scope"] == "subagent"
    assert rows[1]["subagent_name"] == "POWER"


def test_interaction_rows_expose_only_the_new_input_delta_per_turn():
    rows = [
        {
            "session_id": "session-1",
            "proxy_server_request": {"messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "create schematic"},
            ]},
            "response": {},
        },
        {
            "session_id": "session-1",
            "proxy_server_request": {"messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "create schematic"},
                {"role": "assistant", "content": "calling tool"},
                {"role": "tool", "content": "tool result"},
            ]},
            "response": {},
        },
    ]

    enrich_interaction_rows(rows)

    assert rows[0]["current_input_messages"] == [
        {"role": "user", "content": "create schematic"}
    ]
    assert rows[0]["history_message_count"] == 1
    assert rows[1]["current_input_messages"] == [
        {"role": "assistant", "content": "calling tool"},
        {"role": "tool", "content": "tool result"},
    ]
    assert rows[1]["history_message_count"] == 2
