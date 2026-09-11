from pathlib import Path

import pytest

from agent_eval.env_config import effective_environment, env_file_path, load_root_env


def test_loads_quotes_comments_and_export_from_root_env(tmp_path: Path):
    (tmp_path / ".env").write_text(
        """\
# deployment settings
export LITELLM_API_KEY="test-key"
DATABASE_PASSWORD='db-password'
EMPTY=
""",
        encoding="utf-8",
    )

    assert load_root_env(tmp_path) == {
        "LITELLM_API_KEY": "test-key",
        "DATABASE_PASSWORD": "db-password",
        "EMPTY": "",
    }


def test_explicit_environment_wins_over_file(tmp_path: Path):
    (tmp_path / ".env").write_text("VALUE=file\nFILE_ONLY=yes\n", encoding="utf-8")

    assert effective_environment(tmp_path, {"VALUE": "process"}) == {
        "VALUE": "process",
        "FILE_ONLY": "yes",
    }


def test_backend_path_resolves_repository_env(tmp_path: Path):
    backend = tmp_path / "backend"
    backend.mkdir()
    assert env_file_path(backend) == tmp_path / ".env"


def test_invalid_env_line_is_rejected(tmp_path: Path):
    (tmp_path / ".env").write_text("NOT_AN_ASSIGNMENT\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid .env entry"):
        load_root_env(tmp_path)
