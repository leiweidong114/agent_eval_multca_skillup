from pathlib import Path

import httpx
import pytest

from agent_eval.model_config import (
    describe_model_config,
    discover_available_models,
    resolve_model_profile,
    write_codebuddy_profile_config,
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
    assert profile.model_for_agent("claude") == "sonnet"
    assert profile.model_for_agent("openclaw") == "main"
    assert profile.model_for_agent("opencode") == "litellm/MiniMax-M3"
    inline = profile.environment["OPENCODE_CONFIG_CONTENT"]
    assert '"baseURL": "http://127.0.0.1:4000/v1"' in inline
    assert '"apiKey": "{env:LITELLM_API_KEY}"' in inline
    assert "virtual-key" not in inline
    assert '"MiniMax-M3"' in inline
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


def test_claude_uses_bare_mode_and_bearer_auth(tmp_path):
    _write_config(tmp_path)
    profile = resolve_model_profile(
        tmp_path,
        environ={"TEST_LITELLM_KEY": "virtual-key"},
        agent="claude",
    )

    assert profile.agent_args == ("--bare",)
    assert profile.model_for_agent("claude") == "sonnet"
    assert "ANTHROPIC_API_KEY" not in profile.environment


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


def test_native_profile_needs_no_gateway_key(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "models.yaml").write_text(
        """default_profile: native
profiles:
  native:
    type: native
    model: gpt-test
""",
        encoding="utf-8",
    )
    profile = resolve_model_profile(tmp_path, environ={}, agent="codex")
    assert profile.model == "gpt-test"
    assert profile.model_for_agent("claude") == "gpt-test"
    assert profile.environment == {}
    assert profile.agent_args == ()


def test_codebuddy_uses_exact_gateway_model_id(tmp_path):
    _write_config(tmp_path)
    profile = resolve_model_profile(
        tmp_path,
        model_override="opencode-go/minimax-m3",
        environ={"TEST_LITELLM_KEY": "virtual-key"},
        agent="codebuddy",
    )

    assert profile.model_for_agent("codebuddy") == "custom-local:opencode-go/minimax-m3"
    assert profile.model_for_agent("claude") == "sonnet"

    minimax_27 = resolve_model_profile(
        tmp_path,
        model_override="opencode-go/minimax-m2.7",
        environ={"TEST_LITELLM_KEY": "virtual-key"},
        agent="codebuddy",
    )
    assert minimax_27.model_for_agent("codebuddy") == "custom-local:opencode-go/minimax-m2.7"


def test_codebuddy_profile_config_uses_litellm_without_embedding_the_key(tmp_path):
    _write_config(tmp_path)
    profile = resolve_model_profile(
        tmp_path,
        model_override="opencode-go/minimax-m2.7",
        environ={"TEST_LITELLM_KEY": "virtual-key"},
        agent="codebuddy",
    )
    path = tmp_path / "run" / "models.json"

    write_codebuddy_profile_config(path, profile)
    content = path.read_text(encoding="utf-8")

    assert '"id": "opencode-go/minimax-m2.7"' in content
    assert '"url": "http://127.0.0.1:4000/v1/chat/completions"' in content
    assert '"apiKey": "${LITELLM_API_KEY}"' in content
    assert "virtual-key" not in content


def test_openclaw_profile_config_uses_litellm_without_embedding_the_key(tmp_path):
    _write_config(tmp_path)
    profile = resolve_model_profile(
        tmp_path,
        environ={"TEST_LITELLM_KEY": "virtual-key"},
    )
    path = tmp_path / "run" / "openclaw.json"

    workspace = tmp_path / "openclaw-workspace"
    write_openclaw_profile_config(path, profile, workspace=workspace)
    content = path.read_text(encoding="utf-8")

    assert '"primary": "litellm/MiniMax-M3"' in content
    assert '"apiKey": "${LITELLM_API_KEY}"' in content
    assert f'"workspace": "{str(workspace).replace(chr(92), chr(92) * 2)}"' in content
    assert "virtual-key" not in content


def test_discovers_litellm_models_and_keeps_profile_mapping(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("TEST_LITELLM_KEY", "virtual-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer virtual-key"
        return httpx.Response(200, json={"data": [{"id": "gateway/model-a", "owned_by": "test"}]})

    result = discover_available_models(tmp_path, transport=httpx.MockTransport(handler))

    assert result["litellm_available"] is True
    discovered = next(item for item in result["models"] if item["id"] == "gateway/model-a")
    assert discovered["profile"] == "minimax"
    assert discovered["owned_by"] == "test"
