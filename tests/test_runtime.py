import pytest

from agent_eval.runtime import default_agent_command, normalize_agent, skill_target


def test_agent_aliases_and_commands():
    assert normalize_agent("claude_code") == "claude"
    assert normalize_agent("qwen_code") == "qwen"
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
