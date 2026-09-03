from __future__ import annotations

import re
from typing import Any


_SECRET = re.compile(r"(?i)(?:Bearer\s+|sk-)[A-Za-z0-9._-]+")
_URL = re.compile(r"https?://[^\s\"'}]+")


def _safe_text(value: str, *, limit: int = 1200) -> str:
    value = _SECRET.sub("[REDACTED]", value)
    value = _URL.sub("[链接已隐藏]", value)
    return value.strip()[:limit]


def _upstream_message(text: str) -> str:
    usage_limit = re.search(
        r"((?:\d+[- ]hour\s+)?usage limit reached\.?\s*"
        r"Resets? in\s+\d+\s*(?:hr|hour|hours|min|minute|minutes)"
        r"(?:\s+\d+\s*(?:min|minute|minutes))?)",
        text,
        flags=re.I,
    )
    if usage_limit:
        return _safe_text(usage_limit.group(1))
    patterns = (
        r'"message"\s*:\s*"((?:\\.|[^"\\])+)"',
        r"['\"]message['\"]\s*:\s*['\"]([^'\"]+)['\"]",
    )
    candidates: list[str] = []
    for pattern in patterns:
        candidates.extend(re.findall(pattern, text, flags=re.I))
    if candidates:
        value = max(candidates, key=len)
        return _safe_text(value.replace(r"\n", " ").replace(r'\"', '"'))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return _safe_text(max(lines, key=len) if lines else text)


def describe_evaluation_failure(
    text: str,
    *,
    returncode: int = 1,
    status_code: int | None = None,
    component: str = "agent",
) -> dict[str, Any] | None:
    if returncode == 0 and status_code is None:
        return None
    lowered = text.lower()
    reset_match = re.search(r"resets? in\s+([^\"}\n.]+)", text, flags=re.I)
    reset_after = reset_match.group(1).strip() if reset_match else None
    inferred_status = status_code
    if inferred_status is None:
        for code in (429, 401, 403, 500, 502, 503, 504):
            if re.search(rf"(?<!\d){code}(?!\d)", text):
                inferred_status = code
                break

    if any(
        marker in lowered
        for marker in (
            "usage limit", "usage has reached", "quota exceeded", "insufficient quota",
            "insufficient balance", "余额不足", "无可用资源包", "请充值",
        )
    ):
        category = "gateway_quota_exhausted"
        title = "模型使用额度已达上限"
        detail = "上游模型账户的可用额度或时间窗口已经用完。"
        if "5-hour" in lowered or "5 hour" in lowered:
            detail = "OpenCode Go 的 5 小时模型使用额度已经用完。"
        if reset_after:
            detail += f" 服务提示预计 {reset_after} 后重置。"
        action = "等待额度重置，或由管理员补充上游模型账户额度/资源包后重试。"
        if "opencode" in lowered or "gousagelimiterror" in lowered:
            action = "等待额度重置，或在 OpenCode Go 账户启用可用余额后重试。"
        retryable = bool(reset_after)
    elif inferred_status == 429 or any(marker in lowered for marker in ("rate limit", "too many requests", "token plan")):
        category, title = "gateway_rate_limited", "模型服务请求过于频繁"
        detail = "LiteLLM 或上游模型服务返回 HTTP 429，当前请求受到速率限制。"
        action, retryable = "稍后重试；如持续出现，请检查模型账户额度和并发限制。", True
    elif inferred_status == 402:
        category, title = "gateway_quota_exhausted", "模型账户余额不足"
        detail = "上游模型账户拒绝付费请求（HTTP 402），通常表示余额或资源包不足。"
        action, retryable = "由管理员补充上游模型账户额度或资源包后重试。", False
    elif inferred_status == 404 and any(
        marker in lowered for marker in ("/responses", "model group", "model not found")
    ):
        category, title = "model_protocol_incompatible", "模型接口与 Agent 协议不兼容"
        detail = "LiteLLM 已找到模型，但该模型的上游接口不支持 Agent 请求的端点。"
        action = "改用支持该端点的模型，或在 LiteLLM 配置端点转换；Codex 当前要求 Responses API。"
        retryable = False
    elif inferred_status in {500, 502, 503, 504} or "gateway_transport_error" in lowered:
        category, title = "gateway_server_error", "模型网关或上游服务异常"
        detail = f"模型服务返回 HTTP {inferred_status or '5xx'}，暂时无法完成推理。"
        action, retryable = "稍后重试，并检查 LiteLLM 与上游服务日志。", True
    elif any(marker in lowered for marker in ("timeout", "timed out", "connection reset", "connection refused", "temporarily unavailable")):
        category, title = "gateway_unavailable", "模型服务连接失败"
        detail = "连接模型网关时发生超时、断开或服务暂不可用。"
        action, retryable = "检查网络和 LiteLLM 健康状态后重试。", True
    elif inferred_status == 401 or any(marker in lowered for marker in ("unauthorized", "invalid api key", "authentication")):
        category, title = "gateway_authentication", "模型服务鉴权失败"
        detail = "模型网关拒绝了当前 API Key（HTTP 401）。"
        action, retryable = "请管理员检查 API Key 是否有效；不要在评测页面粘贴密钥。", False
    elif inferred_status == 403 or "forbidden" in lowered:
        category, title = "gateway_authorization", "模型服务权限不足"
        detail = "当前身份没有执行该操作或调用该模型的权限（HTTP 403）。"
        if "enterprise" in lowered and "tags" in lowered:
            detail = "当前 LiteLLM 版本不支持 Enterprise 专属的 trace key tags 字段。"
        action, retryable = "请管理员核对 LiteLLM 角色、模型访问范围或版本功能。", False
    elif any(marker in lowered for marker in ("unrecognized_model", "model not found", "unknown model")):
        category, title = "model_incompatible", "指定模型不存在或不兼容"
        detail = "模型网关无法识别当前模型名称，或 Agent 不支持该模型协议。"
        action, retryable = "检查模型 ID、Profile 映射和 Agent 协议配置。", False
    elif any(marker in lowered for marker in ("doctor --fix", "legacy workspace")):
        category, title = "agent_workspace_invalid", "Agent 工作区配置无效"
        detail = "Agent 使用了旧版或损坏的工作区配置。"
        action, retryable = "按照 Agent 的 doctor/修复命令更新工作区后重试。", False
    else:
        category, title = "agent_execution_failed", "Agent 执行失败"
        detail = "Agent 进程异常退出，未生成可验收的结果。"
        action, retryable = "查看技术详情、Agent 日志和本次运行轨迹后重试。", False

    technical_detail = _upstream_message(text)
    return {
        "category": category,
        "retryable": retryable,
        "summary": title,
        "title": title,
        "detail": detail,
        "suggested_action": action,
        "component": component,
        "status_code": inferred_status,
        "reset_after": reset_after,
        "technical_detail": technical_detail,
    }
