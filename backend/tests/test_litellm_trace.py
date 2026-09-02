import httpx
import pytest

from agent_eval.litellm_trace import TraceKeyError, create_trace_key


def _response(status: int, payload: dict, *, headers: dict[str, str] | None = None):
    request = httpx.Request("POST", "http://gateway/key/generate")
    return httpx.Response(status, json=payload, headers=headers, request=request)


def test_trace_key_requires_master_key():
    assert create_trace_key("http://gateway/v1", "model-a", "run-a", master_key="") is None


def test_trace_key_retries_rate_limit(monkeypatch):
    responses = iter([
        _response(429, {"error": "busy"}, headers={"Retry-After": "0"}),
        _response(200, {"key": "sk-run"}),
    ])
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: next(responses))

    key = create_trace_key(
        "http://gateway/v1", "model-a", "run-a", master_key="sk-master"
    )

    assert key.key == "sk-run"
    assert key.alias == "agent-eval-run-a"


def test_trace_key_uses_community_compatible_payload(monkeypatch):
    captured = {}

    def success(*args, **kwargs):
        captured.update(kwargs)
        return _response(200, {"key": "sk-run"})

    monkeypatch.setattr(httpx, "post", success)

    create_trace_key(
        "http://gateway/v1", "model-a", "run-a", master_key="sk-master"
    )

    assert captured["json"]["key_alias"] == "agent-eval-run-a"
    assert captured["json"]["models"] == ["model-a"]
    assert captured["json"]["metadata"] == {"agent_eval_run_id": "run-a"}
    assert "tags" not in captured["json"]


def test_trace_key_does_not_retry_forbidden(monkeypatch):
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _response(403, {"error": "forbidden"})

    monkeypatch.setattr(httpx, "post", forbidden)
    with pytest.raises(TraceKeyError) as captured:
        create_trace_key(
            "http://gateway/v1", "model-a", "run-a", master_key="sk-master"
        )

    assert calls == 1
    assert captured.value.retryable is False
    assert captured.value.status_code == 403
