from __future__ import annotations

import httpx
import pytest

from agent_eval.failure import describe_evaluation_failure
from agent_eval.llm_judge import JudgeGatewayError, _judge_request


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
