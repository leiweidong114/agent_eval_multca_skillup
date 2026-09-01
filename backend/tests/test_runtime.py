import os

import pytest

from agent_eval.runtime import (
    RUNTIME_MANAGED_MODEL_AGENTS,
    SUPPORTED_AGENTS,
    UNSUPPORTED_SKILL_INJECTION_AGENTS,
    agent_capabilities,
    default_agent_command,
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


def test_runtime_discovery_supports_backend_layout_and_legacy_parent(tmp_path):
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

    backend_tool.unlink()
    backend_runtime.unlink()
    legacy_tool = tmp_path / ".tools" / tool_relative[0] / tool_relative[1]
    legacy_runtime = tmp_path / ".runtime" / runtime_relative[0] / "bin" / runtime_relative[1]
    legacy_tool.parent.mkdir(parents=True)
    legacy_runtime.parent.mkdir(parents=True)
    legacy_tool.touch()
    legacy_runtime.touch()
    assert find_skill_up(backend) == legacy_tool.resolve()
    assert find_multica_runtime(backend) == legacy_runtime.resolve()
