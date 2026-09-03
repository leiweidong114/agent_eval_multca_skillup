from pathlib import Path

import httpx
import pytest

from agent_eval.model_config import (
    delete_model_profile,
    describe_model_config,
    discover_available_models,
    load_litellm_model_catalog,
    list_model_profiles,
    refresh_litellm_model_catalog,
    resolve_model_profile,
    save_model_profile,
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
    agent_models:
      claude: claude-sonnet-4-6
      codebuddy: custom-local:MiniMax-M3
      openclaw: main
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
    assert profile.model_for_agent("claude") == "claude-sonnet-4-6"
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
        "-c",
        'model_reasoning_effort="high"',
    )


def test_unified_litellm_gateway_needs_no_profile_and_enables_reasoning(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "models.yaml").write_text(
        """\
litellm:
  model: glm-4.7
  api_base: http://127.0.0.1:4000/v1
  api_key_env: TEST_LITELLM_KEY
  reasoning: true
  agent_models:
    claude: claude-sonnet-4-6
  gateway_models:
    claude: glm-4.7-anthropic
    opencode: glm-4.7
profiles: {}
""",
        encoding="utf-8",
    )

    profile = resolve_model_profile(
        tmp_path,
        model_override="glm-4.7",
        environ={"TEST_LITELLM_KEY": "virtual-key"},
        agent="opencode",
    )

    assert profile.name == "litellm"
    assert profile.model_for_agent("opencode") == "litellm/glm-4.7"
    assert profile.environment["AGENT_EVAL_REASONING_ENABLED"] == "true"
    assert '"reasoning": true' in profile.environment["OPENCODE_CONFIG_CONTENT"]
    description = describe_model_config(tmp_path)
    assert description["configuration_mode"] == "unified_litellm"
    assert description["gateway"] == "litellm"
    assert description["reasoning_enabled"] is True
    assert "profiles" not in description
    assert "default_profile" not in description


def test_unified_litellm_reads_ignored_env_file_and_rejects_no_thinking(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "models.yaml").write_text(
        "litellm:\n  model: glm-4.7\n  api_base: http://fallback.invalid/v1\n",
        encoding="utf-8",
    )
    (config / "litellm.env").write_text(
        "LITELLM_API_BASE=http://127.0.0.1:4000/v1\nLITELLM_API_KEY=virtual-key\n",
        encoding="utf-8",
    )

    profile = resolve_model_profile(tmp_path, model_override="glm-4.7", environ={})

    assert profile.api_base == "http://127.0.0.1:4000/v1"
    with pytest.raises(ValueError, match="reasoning enabled"):
        resolve_model_profile(tmp_path, model_override="glm-4.7-no-thinking", environ={})


def test_claude_uses_bare_mode_and_bearer_auth(tmp_path):
    _write_config(tmp_path)
    profile = resolve_model_profile(
        tmp_path,
        environ={"TEST_LITELLM_KEY": "virtual-key"},
        agent="claude",
    )

    assert profile.agent_args == ("--bare",)
    assert profile.model_for_agent("claude") == "claude-sonnet-4-6"
    assert profile.environment["ANTHROPIC_API_KEY"] == "virtual-key"
    assert profile.environment["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "claude-sonnet-4-6"
    assert profile.environment["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-sonnet-4-6"
    assert profile.environment["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "claude-sonnet-4-6"
    assert profile.environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_opencode_uses_agent_specific_gateway_model(tmp_path):
    _write_config(tmp_path)
    path = tmp_path / "config" / "models.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "    gateway_models:\n      opencode: MiniMax-M3-no-thinking\n",
        encoding="utf-8",
    )
    profile = resolve_model_profile(
        tmp_path,
        environ={"TEST_LITELLM_KEY": "virtual-key"},
        agent="opencode",
    )
    assert profile.model_for_agent("opencode") == "litellm/MiniMax-M3-no-thinking"
    assert '"MiniMax-M3-no-thinking"' in profile.environment["OPENCODE_CONFIG_CONTENT"]


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

    assert profile.model_for_agent("codebuddy") == "custom-local:MiniMax-M3"
    assert profile.model_for_agent("claude") == "claude-sonnet-4-6"

    minimax_27 = resolve_model_profile(
        tmp_path,
        model_override="opencode-go/minimax-m2.7",
        environ={"TEST_LITELLM_KEY": "virtual-key"},
        agent="codebuddy",
    )
    assert minimax_27.model_for_agent("codebuddy") == "custom-local:MiniMax-M2.7"

    arbitrary = resolve_model_profile(
        tmp_path,
        model_override="unsupported/provider-model",
        environ={"TEST_LITELLM_KEY": "virtual-key"},
        agent="codebuddy",
    )
    assert arbitrary.model_for_agent("codebuddy") == "custom-local:provider-model"


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

    assert '"id": "MiniMax-M2.7"' in content
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


def test_refreshes_and_loads_non_secret_litellm_catalog(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("TEST_LITELLM_KEY", "virtual-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "gateway/model-a", "owned_by": "test-owner"}]},
        )

    snapshot = refresh_litellm_model_catalog(
        tmp_path, transport=httpx.MockTransport(handler)
    )
    stored = (tmp_path / "config" / "litellm-models.json").read_text(encoding="utf-8")

    assert snapshot["model_count"] == 1
    assert snapshot["catalog_visible_only"] is True
    assert load_litellm_model_catalog(tmp_path)["models"][0]["id"] == "gateway/model-a"
    assert "profile" not in snapshot["models"][0]
    assert "virtual-key" not in stored


def test_discovered_model_prefers_the_profile_configured_for_its_exact_id(tmp_path, monkeypatch):
    _write_config(tmp_path)
    config_path = tmp_path / "config" / "models.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n  exact:\n"
        + "    type: compatible\n"
        + "    model: gateway/model-a\n"
        + "    api_base: https://litellm.example/v1\n"
        + "    api_key_env: TEST_LITELLM_KEY\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_LITELLM_KEY", "virtual-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gateway/model-a"}]})

    result = discover_available_models(tmp_path, transport=httpx.MockTransport(handler))
    discovered = next(item for item in result["models"] if item["id"] == "gateway/model-a")

    assert discovered["profile"] == "exact"


def test_custom_profile_crud_is_local_atomic_and_never_exposes_key(tmp_path):
    _write_config(tmp_path)

    saved = save_model_profile(
        tmp_path,
        "company_gateway",
        {
            "model": "vendor/model-a",
            "api_base": "https://gateway.example/v1",
            "api_key_env": "COMPANY_GATEWAY_KEY",
            "protocol": "openai_compatible",
            "context_window": 128000,
            "max_output_tokens": 16000,
            "agent_models": {"claude": "sonnet"},
            "gateway_models": {"claude": "vendor/model-a-anthropic"},
        },
        api_key="top-secret",
        make_default=True,
    )

    assert saved["compatible_agents"] and len(saved["compatible_agents"]) == 21
    assert saved["supports_all_evaluation_agents"] is True
    assert saved["api_key_configured"] is True
    assert "top-secret" not in repr(saved)
    assert "top-secret" not in (tmp_path / "config" / "local.yaml").read_text(encoding="utf-8")
    assert "COMPANY_GATEWAY_KEY=top-secret" in (
        tmp_path / "config" / "secrets.env"
    ).read_text(encoding="utf-8")

    resolved = resolve_model_profile(
        tmp_path, profile_name="company_gateway", agent="claude", environ={}
    )
    assert resolved.protocol == "openai_compatible"
    assert resolved.context_window == 128000
    assert resolved.environment["OPENAI_API_KEY"] == "top-secret"
    assert len(list_model_profiles(tmp_path)) == 2

    assert delete_model_profile(tmp_path, "company_gateway") is True
    assert delete_model_profile(tmp_path, "company_gateway") is False
    assert {item["name"] for item in list_model_profiles(tmp_path)} == {"minimax"}


def test_protocol_specific_profile_rejects_an_incompatible_agent(tmp_path):
    _write_config(tmp_path)
    save_model_profile(
        tmp_path,
        "anthropic_direct",
        {
            "model": "claude-test",
            "api_base": "https://anthropic.example",
            "api_key_env": "ANTHROPIC_DIRECT_KEY",
            "protocol": "anthropic_messages",
        },
        api_key="secret",
    )

    claude = resolve_model_profile(
        tmp_path, profile_name="anthropic_direct", agent="claude", environ={}
    )
    assert claude.protocol == "anthropic_messages"
    with pytest.raises(ValueError, match="requires openai_responses"):
        resolve_model_profile(
            tmp_path, profile_name="anthropic_direct", agent="codex", environ={}
        )
