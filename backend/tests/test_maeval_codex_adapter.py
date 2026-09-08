from pathlib import Path

from maeval.adapters import CodexCliAdapter
from maeval.models import Candidate, ScorerSpec, Task


def test_codex_litellm_request_uses_protocol_compatibility_proxy(monkeypatch):
    state: dict[str, object] = {}

    class FakeProxy:
        openai_base_url = "http://127.0.0.1:45678/v1"

        def __init__(self, upstream_url, **kwargs):
            state["upstream_url"] = upstream_url
            state["proxy_options"] = kwargs

        def start(self):
            state["started"] = True

        def close(self):
            state["closed"] = True

    def fake_run_process(command, *, cwd, timeout_seconds, env):
        state["command"] = command
        state["env"] = env
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("B", encoding="utf-8")
        stdout = '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":1}}\n'
        return 0, stdout, "", 25

    monkeypatch.setattr(
        "agent_eval.codebuddy_proxy.CodeBuddyCompatibilityProxy", FakeProxy
    )
    monkeypatch.setattr("maeval.adapters._run_process", fake_run_process)

    result = CodexCliAdapter(agent_mode=False).run(
        Candidate(
            id="codex-glm",
            adapter="codex_cli_direct",
            model="glm-4.5-air",
            base_url="http://litellm.example/v1",
            api_key="secret",
        ),
        Task(
            id="arc-1",
            kind="direct",
            prompt="Choose A or B",
            scorer=ScorerSpec(type="exact", expected="B"),
        ),
        None,
    )

    assert result.ok
    assert result.text == "B"
    assert result.input_tokens == 10
    assert result.output_tokens == 1
    assert state["upstream_url"] == "http://litellm.example/v1"
    assert state["started"] is True
    assert state["closed"] is True
    assert 'model_providers.litellm.base_url="http://127.0.0.1:45678/v1"' in state[
        "command"
    ]
    assert state["env"]["OPENAI_API_KEY"] == "secret"
