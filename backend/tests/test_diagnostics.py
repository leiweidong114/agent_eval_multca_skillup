from types import SimpleNamespace

import httpx

from agent_eval import diagnostics


class _FakeClient:
    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"data": [{"id": "missing-model"}]},
        )

    def post(self, url, **_kwargs):
        return httpx.Response(
            400,
            request=httpx.Request("POST", url),
            json={
                "error": {
                    "message": "model not found; Authorization: Bearer sk-secret-value"
                }
            },
        )


def test_litellm_check_keeps_safe_provider_failure_detail(tmp_path, monkeypatch):
    profile = SimpleNamespace(
        api_base="http://litellm.invalid/v1",
        api_key_env="LITELLM_API_KEY",
        environment={"LITELLM_API_KEY": "sk-secret-value"},
    )
    monkeypatch.setattr(diagnostics, "resolve_model_profile", lambda *_args, **_kwargs: profile)
    monkeypatch.setattr(diagnostics, "gateway_request_headers", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(diagnostics.httpx, "Client", _FakeClient)

    result = diagnostics.check_litellm(tmp_path, model="missing-model")

    assert result["status"] == "failed"
    assert result["status_code"] == 400
    assert result["failure"]["category"] == "model_incompatible"
    assert "model not found" in result["failure"]["technical_detail"]
    assert "sk-secret-value" not in result["failure"]["technical_detail"]
