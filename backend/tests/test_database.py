from datetime import datetime
from pathlib import Path

import pytest

from agent_eval.database import (
    DatabaseConfigurationError,
    _database_retry,
    resolve_database_config,
    summarize_model_interactions,
    verify_requested_model,
)


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
