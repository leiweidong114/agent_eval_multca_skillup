import os

import pytest

from agent_eval.agent_adapters import AGENT_MODEL_ADAPTERS, EXCLUDED_AGENT_ADAPTERS

from agent_eval.runtime import (
    RUNTIME_MANAGED_MODEL_AGENTS,
    SUPPORTED_AGENTS,
    UNSUPPORTED_SKILL_INJECTION_AGENTS,
    agent_capabilities,
    default_agent_command,
    load_agent_paths,
    save_agent_path,
    find_multica_runtime,
    find_skill_up,
    normalize_agent,
    backend_agent,
    skill_target,
    validate_evaluation_capabilities,
)


def test_agent_aliases_and_commands():
    assert normalize_agent("claude_code") == "claude"
    assert normalize_agent("qwen_code") == "qwen"
    assert normalize_agent("justdo") == "justdo"
    assert backend_agent("justdo") == "openclaw"
    assert default_agent_command("qodercli") == "qodercli"
    with pytest.raises(ValueError, match="Unsupported Agent"):
        normalize_agent("not-a-real-agent")


def test_saved_agent_path_is_shared_by_runtime_discovery(tmp_path, monkeypatch):
    executable = tmp_path / ("custom-agent.cmd" if os.name == "nt" else "custom-agent")
    executable.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n", encoding="utf-8")
    if os.name != "nt":
        executable.chmod(0o755)

    saved = save_agent_path("justdo", str(executable), project_root=tmp_path)

    assert saved["justdo"] == str(executable.resolve())
    assert load_agent_paths(tmp_path) == saved
    monkeypatch.delenv("JUSTDO_AGENT_EXECUTABLE", raising=False)
    assert default_agent_command("justdo", tmp_path) == str(executable.resolve())


def test_empty_saved_agent_path_restores_automatic_discovery(tmp_path):
    executable = tmp_path / ("custom-agent.cmd" if os.name == "nt" else "custom-agent")
    executable.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n", encoding="utf-8")
    if os.name != "nt":
        executable.chmod(0o755)
    save_agent_path("justdo", str(executable), project_root=tmp_path)

    assert save_agent_path("justdo", "", project_root=tmp_path) == {}


def test_skill_target_matches_agent_native_discovery():
    assert skill_target("codex", "demo") == ".agents/skills/demo"
    assert skill_target("claude_code", "demo") == ".claude/skills/demo"
    assert skill_target("qwen_code", "demo") == ".qwen/skills/demo"
    assert skill_target("mcode", "demo") == ".minimax/skills/demo"
    assert skill_target("qwenpaw", "demo") == "skill_pool/demo"
    assert skill_target("omp", "demo") == ".omp/skills/demo"


def test_every_supported_agent_has_an_explicit_evaluation_capability():
    assert RUNTIME_MANAGED_MODEL_AGENTS == {"mcode", "qwenpaw", "zeroclaw"}
    assert UNSUPPORTED_SKILL_INJECTION_AGENTS == {"dim", "hermes", "zeroclaw"}
    for agent in SUPPORTED_AGENTS:
        capabilities = agent_capabilities(agent)
        assert capabilities["agent"] == agent
        assert capabilities["specified_model_and_skill_evaluation"] == (
            agent not in RUNTIME_MANAGED_MODEL_AGENTS
            and agent not in UNSUPPORTED_SKILL_INJECTION_AGENTS
        )
        if capabilities["skill_injection"]:
            assert skill_target(agent, "matrix-skill").endswith("/matrix-skill")
            validate_evaluation_capabilities(
                agent,
                require_model_selection=agent not in RUNTIME_MANAGED_MODEL_AGENTS,
            )


def test_model_adapter_registry_has_exactly_the_21_supported_agents():
    supported_by_capability = {
        agent
        for agent in SUPPORTED_AGENTS
        if agent_capabilities(agent)["specified_model_and_skill_evaluation"]
    }

    assert len(AGENT_MODEL_ADAPTERS) == 21
    assert set(AGENT_MODEL_ADAPTERS) == supported_by_capability
    assert set(EXCLUDED_AGENT_ADAPTERS) == set(SUPPORTED_AGENTS) - supported_by_capability
    assert all(
        agent_capabilities(agent)["model_adapter"]["evaluation_supported"]
        for agent in AGENT_MODEL_ADAPTERS
    )


def test_six_primary_agents_advertise_explicit_subagent_transports():
    expected = {"claude", "codebuddy", "codex", "justdo", "openclaw", "opencode"}

    for agent in expected:
        capabilities = agent_capabilities(agent)
        assert capabilities["subagent_supported"] is True
        assert capabilities["subagent_transport"]


@pytest.mark.parametrize("agent", sorted(UNSUPPORTED_SKILL_INJECTION_AGENTS))
def test_agents_without_direct_skill_adapter_fail_before_execution(agent):
    with pytest.raises(ValueError, match="specified Skill"):
        validate_evaluation_capabilities(agent)


@pytest.mark.parametrize(
    "agent", sorted(RUNTIME_MANAGED_MODEL_AGENTS - UNSUPPORTED_SKILL_INJECTION_AGENTS)
)
def test_runtime_managed_models_require_explicit_opt_out(agent):
    with pytest.raises(ValueError, match="specified model"):
        validate_evaluation_capabilities(agent)
    validate_evaluation_capabilities(agent, require_model_selection=False)


def test_runtime_discovery_uses_backend_layout(tmp_path):
    backend = tmp_path / "backend"
    tool_relative = (
        ("windows", "skill-up.exe") if os.name == "nt" else ("linux", "skill-up")
    )
    runtime_relative = (
        ("windows", "multica-eval-runtime.exe")
        if os.name == "nt"
        else ("linux", "multica-eval-runtime")
    )

    backend_tool = backend / ".tools" / tool_relative[0] / tool_relative[1]
    backend_runtime = backend / ".runtime" / runtime_relative[0] / "bin" / runtime_relative[1]
    backend_tool.parent.mkdir(parents=True)
    backend_runtime.parent.mkdir(parents=True)
    backend_tool.touch()
    backend_runtime.touch()
    assert find_skill_up(backend) == backend_tool.resolve()
    assert find_multica_runtime(backend) == backend_runtime.resolve()
