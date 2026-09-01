from pathlib import Path

from agent_eval.agent_config import describe_agents, resolve_agent_executable


def test_agent_config_uses_local_override_and_environment_placeholders(tmp_path: Path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "agents.yaml").write_text(
        "agents:\n  codex:\n    executable: C:\\\\default\\\\codex.exe\n",
        encoding="utf-8",
    )
    (config / "local.yaml").write_text(
        "agents:\n  codex:\n    executable: '${AGENT_HOME}\\codex.exe'\n",
        encoding="utf-8",
    )
    assert resolve_agent_executable(
        tmp_path, "codex", environ={"AGENT_HOME": "D:\\tools"}
    ) == "D:\\tools\\codex.exe"


def test_explicit_agent_executable_has_highest_precedence(tmp_path: Path):
    (tmp_path / "config").mkdir()
    assert resolve_agent_executable(tmp_path, "codex", "X:\\codex.exe") == "X:\\codex.exe"
    assert any(item["agent"] == "codex" for item in describe_agents(tmp_path))
