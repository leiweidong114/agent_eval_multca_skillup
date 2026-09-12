from __future__ import annotations

import httpx
import pytest

from agent_eval.failure import describe_evaluation_failure


def test_domain_numbers_are_not_misclassified_as_http_statuses():
    failure = describe_evaluation_failure(
        "assertion failed: continuous current must stay below 500mA",
        returncode=1,
    )
    assert failure is not None
    assert failure["category"] == "agent_execution_failed"
    assert failure["status_code"] is None
from types import SimpleNamespace

from agent_eval.llm_judge import JudgeGatewayError, _judge_request, run_llm_judge


def test_opencode_usage_limit_has_actionable_reset_information():
    failure = describe_evaluation_failure(
        '{"error":{"type":"GoUsageLimitError","message":"5-hour usage limit reached. Resets in 2hr 38min. To continue, enable usage from your available balance: https://opencode.ai/workspace/private/go"}}',
        status_code=429,
    )

    assert failure is not None
    assert failure["category"] == "gateway_quota_exhausted"
    assert failure["retryable"] is True
    assert failure["reset_after"] == "2hr 38min"
    assert "5 小时" in failure["detail"]
    assert "启用可用余额" in failure["suggested_action"]
    assert "opencode.ai" not in failure["technical_detail"]
    assert failure["technical_detail"] == "5-hour usage limit reached. Resets in 2hr 38min"


def test_failure_reporting_redacts_api_keys():
    failure = describe_evaluation_failure(
        "HTTP 401 invalid api key sk-secret-value", status_code=401
    )

    assert failure is not None
    assert failure["category"] == "gateway_authentication"
    assert "sk-secret-value" not in failure["technical_detail"]


def test_chinese_balance_error_is_classified_as_quota_exhausted():
    failure = describe_evaluation_failure(
        '{"error":{"message":"余额不足或无可用资源包,请充值。"}}',
        status_code=429,
    )

    assert failure is not None
    assert failure["category"] == "gateway_quota_exhausted"
    assert failure["retryable"] is False
    assert "额度/资源包" in failure["suggested_action"]


def test_responses_404_is_classified_as_agent_protocol_incompatible():
    failure = describe_evaluation_failure(
        '{"error":{"message":"Not Found","path":"/v4/responses"}}',
        status_code=404,
    )

    assert failure is not None
    assert failure["category"] == "model_protocol_incompatible"
    assert "Responses API" in failure["suggested_action"]


def test_justdo_not_running_is_actionable():
    failure = describe_evaluation_failure(
        "JustDo is not running. Start JustDo and keep it open or in the tray. exit status 69",
        returncode=69,
    )

    assert failure is not None
    assert failure["category"] == "agent_bridge_unavailable"
    assert "完全退出 JustDo" in failure["suggested_action"]


def test_judge_rate_limit_preserves_upstream_reason(monkeypatch):
    request = httpx.Request("POST", "http://gateway/v1/chat/completions")
    response = httpx.Response(
        429,
        request=request,
        json={
            "error": {
                "type": "GoUsageLimitError",
                "message": "5-hour usage limit reached. Resets in 1hr 5min.",
            }
        },
    )
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    monkeypatch.setattr("agent_eval.llm_judge.time.sleep", lambda *_: None)

    with pytest.raises(JudgeGatewayError) as captured:
        _judge_request(
            "http://gateway/v1/chat/completions",
            headers={},
            body={},
            timeout=1,
        )

    assert captured.value.failure["category"] == "gateway_quota_exhausted"
    assert captured.value.failure["reset_after"] == "1hr 5min"


def test_agent_case_timeout_is_not_reported_as_gateway_failure():
    failure = describe_evaluation_failure(
        "context deadline exceeded (case timeout 1200s via cases.defaults.timeout_seconds)",
        returncode=1,
    )
    assert failure["category"] == "agent_timeout"
    assert failure["component"] == "agent"


def test_system_message_order_is_protocol_incompatibility():
    failure = describe_evaluation_failure(
        "HTTP 400 System message must be at the beginning", status_code=400
    )
    assert failure["category"] == "model_protocol_incompatible"


def test_llm_judge_forwards_employee_number_to_gateway_headers(monkeypatch, tmp_path):
    profile = SimpleNamespace(
        api_base="http://gateway/v1",
        model="judge-model",
        name="litellm",
        environment={"LITELLM_API_KEY": "secret"},
    )
    observed = {}
    monkeypatch.setattr("agent_eval.llm_judge.load_runtime_settings", lambda *_: {})
    monkeypatch.setattr("agent_eval.llm_judge.resolve_model_profile", lambda *_, **__: profile)
    monkeypatch.setattr(
        "agent_eval.llm_judge.gateway_request_headers",
        lambda project_root, selected, employee_no: observed.setdefault(
            "employee_no", employee_no
        )
        and {"X-Agent-Eval-User": employee_no},
    )
    request = httpx.Request("POST", "http://gateway/v1/chat/completions")
    response = httpx.Response(
        200,
        request=request,
        json={
            "choices": [{"message": {"content": '{"dimensions":{"result":{"score":90},"process":{"score":80},"skill_quality":{"score":70}}}'}}],
            "usage": {},
        },
    )
    monkeypatch.setattr("agent_eval.llm_judge._judge_request", lambda *_, **__: response)

    result = run_llm_judge(
        project_root=tmp_path,
        scoring_config={"llm_judge": {"enabled": True, "model": "judge-model"}},
        evidence={},
        employee_no="E10001",
    )

    assert result["status"] == "completed"
    assert observed["employee_no"] == "E10001"
