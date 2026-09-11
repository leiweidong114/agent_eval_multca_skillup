from datetime import datetime
from pathlib import Path

import pytest

from agent_eval.database import (
    DatabaseConfig,
    DatabaseConfigurationError,
    _database_retry,
    _sanitize,
    resolve_database_config,
    summarize_model_interactions,
    verify_requested_model,
    fetch_model_interactions,
)


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
