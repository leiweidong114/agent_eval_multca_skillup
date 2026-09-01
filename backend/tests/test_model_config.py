from pathlib import Path

import pytest

from agent_eval.model_config import (
    describe_model_config,
    resolve_model_profile,
    write_openclaw_profile_config,
)


def _write_config(root: Path) -> None:
    config = root / "config"
    config.mkdir()
    (config / "models.yaml").write_text(
        """\
default_profile: minimax
profiles:
  minimax:
    model: MiniMax-M3
    api_base: http://127.0.0.1:4000/v1
    api_key_env: TEST_LITELLM_KEY
""",
        encoding="utf-8",
    )


def test_resolves_default_litellm_profile_and_agent_environment(tmp_path):
    _write_config(tmp_path)

    profile = resolve_model_profile(
        tmp_path,
        environ={"TEST_LITELLM_KEY": "virtual-key"},
        agent="codex",
    )

    assert profile.name == "minimax"
    assert profile.model == "MiniMax-M3"
    assert profile.api_base == "http://127.0.0.1:4000/v1"
    assert profile.environment["OPENAI_BASE_URL"] == "http://127.0.0.1:4000/v1"
    assert profile.environment["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4000"
    assert profile.environment["OPENAI_API_KEY"] == "virtual-key"
    assert profile.environment["ANTHROPIC_AUTH_TOKEN"] == "virtual-key"
    assert profile.model_for_agent("codex") == "MiniMax-M3"
    assert profile.model_for_agent("openclaw") == "main"
    assert profile.agent_args == (
        "-c",
        'model_provider="litellm"',
        "-c",
        'model_providers.litellm.name="LiteLLM"',
        "-c",
        'model_providers.litellm.base_url="http://127.0.0.1:4000/v1"',
        "-c",
        'model_providers.litellm.env_key="LITELLM_API_KEY"',
        "-c",
        'model_providers.litellm.wire_api="responses"',
    )


def test_local_config_overrides_model_without_committing_a_key(tmp_path):
    _write_config(tmp_path)
    (tmp_path / "config" / "local.yaml").write_text(
        """\
profiles:
  minimax:
    model: MiniMax-M3-test
secrets:
  TEST_LITELLM_KEY: local-key
""",
        encoding="utf-8",
    )

    profile = resolve_model_profile(tmp_path, environ={})
    description = describe_model_config(tmp_path)

    assert profile.model == "MiniMax-M3-test"
    assert profile.environment["LITELLM_API_KEY"] == "local-key"
    assert description["default_model"] == "MiniMax-M3-test"
    assert description["api_key_configured"] is True
    assert description["profile_models"] == {"minimax": "MiniMax-M3-test"}


def test_missing_virtual_key_is_rejected(tmp_path):
    _write_config(tmp_path)

    with pytest.raises(ValueError, match="TEST_LITELLM_KEY"):
        resolve_model_profile(tmp_path, environ={})


def test_openclaw_profile_config_uses_litellm_without_embedding_the_key(tmp_path):
    _write_config(tmp_path)
    profile = resolve_model_profile(
        tmp_path,
        environ={"TEST_LITELLM_KEY": "virtual-key"},
    )
    path = tmp_path / "run" / "openclaw.json"

    write_openclaw_profile_config(path, profile)
    content = path.read_text(encoding="utf-8")

    assert '"primary": "litellm/MiniMax-M3"' in content
    assert '"apiKey": "${LITELLM_API_KEY}"' in content
    assert "virtual-key" not in content
