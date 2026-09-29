from __future__ import annotations

import json

from maeval.adapters import OpenAiHttpAdapter
from maeval.models import Candidate, ScorerSpec, Task


def test_openai_adapter_forwards_configured_gateway_headers(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"model": "model-a", "choices": [{"message": {"content": "OK"}}]}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("maeval.adapters.urllib.request.urlopen", fake_urlopen)
    result = OpenAiHttpAdapter().run(
        Candidate(
            id="gateway",
            adapter="openai_http",
            model="model-a",
            base_url="http://gateway.local/v1",
            api_key="secret",
            request_headers={
                "User-Agent": "agent-eval/test",
                "x-cookie": "cookie-value",
                "x-user-account": "employee-1",
            },
        ),
        Task(
            id="q1",
            kind="direct",
            prompt="hi",
            scorer=ScorerSpec(type="exact", expected="OK"),
        ),
        None,
    )

    assert result.ok is True
    assert captured["headers"]["user-agent"] == "agent-eval/test"
    assert captured["headers"]["x-cookie"] == "cookie-value"
    assert captured["headers"]["x-user-account"] == "employee-1"
    assert captured["headers"]["authorization"] == "Bearer secret"
