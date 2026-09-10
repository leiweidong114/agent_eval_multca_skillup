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
    assert args.timeout == 15.0
    assert args.workers == 8
    assert args.agent is None
    assert args.show_unavailable is False


def test_check_agent_subagent_probe_defaults():
    from agent_eval import cli

    args = cli._parser().parse_args(
        ["check-agent", "--agent", "codex", "--model", "glm-4.5-air", "--verify-subagent"]
    )

    assert args.verify_subagent is True
    assert args.max_turns == 8
    assert args.extra_arg == []


def test_agents_command_defaults_to_available_executables_only(monkeypatch, capsys):
    from agent_eval import cli

    monkeypatch.setattr(cli, "SUPPORTED_AGENTS", ("codex", "opencode"))
    monkeypatch.setattr(cli, "default_agent_command", lambda agent: agent)
    monkeypatch.setattr(
        cli.shutil, "which", lambda command: "codex.exe" if command == "codex" else None
    )
    monkeypatch.setattr(
        cli, "_probe_local_agent",
        lambda executable: {"available": True, "version": "test", "exit_code": 0, "error": None},
    )
    monkeypatch.setattr(
        cli, "agent_capabilities",
        lambda agent: {"specified_model_and_skill_evaluation": True},
    )
    monkeypatch.setattr(cli, "describe_agent_contract", lambda agent: {"agent": agent})
    monkeypatch.setattr(cli, "_parser", lambda: type(
        "Parser", (), {"parse_args": lambda self: argparse.Namespace(command="agents", all=False)}
    )())

    cli.main()

    rows = json.loads(capsys.readouterr().out)
    assert [row["agent"] for row in rows] == ["codex"]


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


def test_subagent_verification_rejects_a_parent_simulation():
    from agent_eval.cli import _verify_subagent_evidence

    result = _verify_subagent_evidence(
        "justdo",
        "glm-4.5-air",
        {"final_message": "PARENT_OK:SUBAGENT_OK"},
        [
            {
                "status": "success",
                "model": "glm-4.5-air",
                "proxy_server_request": {"messages": [
                    {"role": "assistant", "tool_calls": [{
                        "function": {"name": "exec", "arguments": '{"command":"python fake.py"}'},
                    }]},
                    {"role": "tool", "content": "SUBAGENT_OK"},
                ]},
            }
        ],
    )

    assert result["verified"] is False
    assert "subagent_invocation" in result["reason"]


def test_subagent_verification_rejects_unrelated_shell_marker_after_spawn():
    from agent_eval.cli import _verify_subagent_evidence

    rows = [
        {
            "status": "success",
            "model": "glm-4.5-air",
            "proxy_server_request": {"messages": [
                {"role": "assistant", "tool_calls": [{
                    "id": "spawn-1",
                    "function": {"name": "multi_agent_v1__spawn_agent", "arguments": "{}"},
                }]},
                {"role": "assistant", "tool_calls": [{
                    "id": "shell-1",
                    "function": {"name": "shell_command", "arguments": "{}"},
                }]},
                {"role": "tool", "tool_call_id": "shell-1", "content": "SUBAGENT_OK"},
            ]},
        },
        {"status": "success", "model": "glm-4.5-air"},
    ]

    result = _verify_subagent_evidence(
        "codex", "glm-4.5-air", {"final_message": "PARENT_OK:SUBAGENT_OK"}, rows
    )

    assert result["verified"] is False
    assert "successful_child_tool_result" in result["reason"]


def test_subagent_verification_accepts_justdo_child_cli_evidence():
    from agent_eval.cli import _verify_subagent_evidence

    rows = [
        {
            "status": "success",
            "model": "glm-4.5-air",
            "proxy_server_request": {"messages": [{
                "role": "assistant",
                "tool_calls": [{"function": {
                    "name": "exec",
                    "arguments": '{"command":"agent-eval check-agent --agent justdo --model glm-4.5-air"}',
                }}],
            }]},
        },
        {
            "status": "success",
            "model": "glm-4.5-air",
            "proxy_server_request": {"messages": [{
                "role": "tool",
                "content": '{"status": "connected", "model": "glm-4.5-air", "response": "SUBAGENT_OK"}',
            }]},
        },
    ]

    result = _verify_subagent_evidence(
        "justdo", "glm-4.5-air", {"final_message": "PARENT_OK:SUBAGENT_OK"}, rows
    )

    assert result["verified"] is True
    assert result["transport"] == "isolated_child_process"


def test_subagent_verification_accepts_justdo_native_session_spawn():
    from agent_eval.cli import _verify_subagent_evidence

    rows = [
        {
            "status": "success",
            "model": "glm-4.5-air",
            "proxy_server_request": {"messages": [
                {"role": "assistant", "tool_calls": [{"function": {
                    "name": "sessions_spawn",
                    "arguments": '{"task":"Reply with exactly SUBAGENT_OK"}',
                }}]},
            ]},
        },
        {
            "status": "success",
            "model": "glm-4.5-air",
            "response": {"choices": [{"message": {
                "role": "assistant", "content": "SUBAGENT_OK",
            }}]},
        },
    ]

    result = _verify_subagent_evidence(
        "justdo", "glm-4.5-air", {"final_message": "PARENT_OK:SUBAGENT_OK"}, rows
    )

    assert result["verified"] is True
    assert result["transport"] == "native"


def test_subagent_verification_accepts_justdo_sessions_yield_result():
    from agent_eval.cli import _verify_subagent_evidence

    rows = [
        {
            "status": "success",
            "model": "glm-4.5-air",
            "proxy_server_request": {"messages": [
                {"role": "assistant", "tool_calls": [{
                    "id": "spawn-1",
                    "function": {
                        "name": "sessions_spawn",
                        "arguments": '{"task":"Reply with exactly SUBAGENT_OK"}',
                    },
                }]},
            ]},
        },
        {
            "status": "success",
            "model": "glm-4.5-air",
            "proxy_server_request": {"messages": [
                {"role": "assistant", "tool_calls": [{
                    "id": "yield-1",
                    "function": {"name": "sessions_yield", "arguments": "{}"},
                }]},
                {
                    "role": "tool",
                    "tool_call_id": "yield-1",
                    "content": json.dumps({
                        "status": "completed",
                        "results": [{
                            "sessionKey": "agent:main:subagent:child-1",
                            "status": "ok",
                            "result": "SUBAGENT_OK",
                        }],
                    }),
                },
            ]},
        },
    ]

    result = _verify_subagent_evidence(
        "justdo", "glm-4.5-air", {"final_message": "PARENT_OK:SUBAGENT_OK"}, rows
    )

    assert result["verified"] is True
    assert result["transport"] == "native"
