from datetime import datetime
from pathlib import Path

import pytest

from agent_eval.database import (
    DatabaseConfigurationError,
    resolve_database_config,
    summarize_model_interactions,
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
