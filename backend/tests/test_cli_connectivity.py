import argparse
import json


def test_models_command_parses_refresh_and_prefix():
    from agent_eval import cli

    args = cli._parser().parse_args(
        ["models", "--refresh", "--prefix", "opencode-go/"]
    )

    assert args.command == "models"
    assert args.refresh is True
    assert args.prefix == "opencode-go/"


def test_connectivity_probe_accepts_exact_custom_prompt(tmp_path, monkeypatch):
    """The live matrix must be able to send exactly HI, without marker text."""
    from agent_eval import cli

    captured = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    class Profile:
        api_base = ""
        model = "test-model"
        environment = {}
        agent_args = ()

        @staticmethod
        def model_for_agent(agent):
            return "test-model"

    monkeypatch.setattr(cli, "agent_capabilities", lambda agent: {"model_selection": True})
    monkeypatch.setattr(cli, "backend_agent", lambda agent: agent)
    monkeypatch.setattr(cli, "resolve_model_profile", lambda *args, **kwargs: Profile())
    monkeypatch.setattr(cli.shutil, "which", lambda executable: "agent.exe")
    monkeypatch.setattr(cli, "find_multica_runtime", lambda root: tmp_path / "runtime.exe")

    def fake_run(command, **kwargs):
        input_path = command[command.index("--input") + 1]
        output_path = command[command.index("--output") + 1]
        captured.update(json.loads(open(input_path, encoding="utf-8").read()))
        with open(output_path, "w", encoding="utf-8") as stream:
            json.dump({"exit_code": 0, "final_message": "hello"}, stream)
        return Completed()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    result = cli._check_agent(
        argparse.Namespace(
            agent="codex", profile="test", model=None, agent_executable=None,
            timeout=10, database_verify=False, prompt="HI",
        )
    )

    assert captured["messages"] == [{"role": "user", "content": "HI"}]
    assert result["status"] == "connected"
