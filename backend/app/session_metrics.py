from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping


METRIC_DEFINITION_VERSION = "1.1.0-task-classifier"
_FAILURE_PATTERN = re.compile(r"\b(error|failed|failure|exception|timeout|timed out)\b|失败|错误|异常|超时", re.I)
_SCRIPT_PATTERN = re.compile(r"(?:^|[\\/\s\"'])([^\\/\s\"']+\.(?:py|ps1|sh|js|mjs|cjs))(?:$|[\s\"'])", re.I)


def _json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def _messages(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    request = _json(row.get("proxy_server_request")) or {}
    request = _json(request.get("body")) if isinstance(request, dict) and request.get("body") else request
    value = request.get("messages") if isinstance(request, dict) else None
    value = value or _json(row.get("messages"))
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _response_messages(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    response = _json(row.get("response")) or {}
    if not isinstance(response, dict):
        return []
    choices = response.get("choices")
    if isinstance(choices, list):
        return [message for choice in choices if isinstance(choice, dict) for message in [choice.get("message")] if isinstance(message, dict)]
    output = response.get("output")
    return [item for item in output if isinstance(item, dict)] if isinstance(output, list) else []


def _tool_calls(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = message.get("tool_calls")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _tool_name_and_arguments(call: Mapping[str, Any]) -> tuple[str, str]:
    function = call.get("function") if isinstance(call.get("function"), dict) else call
    return str(function.get("name") or call.get("name") or "unknown"), str(function.get("arguments") or call.get("arguments") or "")


def _result_failed(message: Mapping[str, Any]) -> bool:
    content = str(message.get("content") or "")
    status = str(message.get("status") or "").casefold()
    return status in {"failed", "failure", "error"} or bool(_FAILURE_PATTERN.search(content[:2000]))


def _task_type(evidence_text: str) -> tuple[str, str, float]:
    lower = evidence_text.casefold()
    signal = "signal-interface-generation" in lower or "sheets.json" in lower
    layout = "schematic-layout-codegen" in lower or "layout_sheet.py" in lower or "codegen_base.py" in lower
    apply = "schematic-web-apply" in lower or "apply.py" in lower
    if signal and (layout or apply):
        return "block_to_schematic", "rule_evidence", 0.9
    if signal:
        return "block_to_signal_list", "rule_evidence", 0.82
    if layout or apply:
        return "signal_list_to_schematic", "rule_evidence", 0.72
    return "other", "rule_fallback", 0.5


def _skill_steps(task_type: str) -> list[tuple[str, tuple[str, ...]]]:
    signal = ("signal-interface-generation", "sheets.json")
    validate = ("validate_sheets.py",)
    layout = ("schematic-layout-codegen", "layout_sheet.py", "codegen_base.py")
    apply = ("schematic-web-apply", "apply.py")
    return {
        "block_to_schematic": [("generate_signal_list", signal), ("validate_signal_list", validate), ("generate_layout", layout), ("apply_schematic", apply)],
        "block_to_signal_list": [("generate_signal_list", signal), ("validate_signal_list", validate)],
        "signal_list_to_schematic": [("generate_layout", layout), ("apply_schematic", apply)],
    }.get(task_type, [])


def calculate_rule_metrics(conversation: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate reproducible metrics and explicitly mark evidence gaps."""
    rows = [row for row in conversation.get("timeline", []) if isinstance(row, dict)]
    rows.sort(key=lambda row: (str(row.get("start_time") or ""), str(row.get("request_id") or "")))
    evidence_text = json.dumps(rows, ensure_ascii=False, default=str)
    task_type, task_source, task_confidence = _task_type(evidence_text)
    calls: list[dict[str, Any]] = []
    results: dict[str, dict[str, Any]] = {}
    interaction_errors: set[str] = set()
    for row in rows:
        if str(row.get("status") or "").casefold() in {"failed", "failure", "error"}:
            metadata = _json(row.get("metadata")) or {}
            detail = json.dumps(metadata.get("error_information") or row.get("status"), ensure_ascii=False, default=str)
            interaction_errors.add(re.sub(r"\s+", " ", detail)[:300])
        for message in [*_messages(row), *_response_messages(row)]:
            role = str(message.get("role") or "").casefold()
            if role in {"tool", "function"}:
                call_id = str(message.get("tool_call_id") or message.get("call_id") or "")
                if call_id:
                    results[call_id] = message
            for call in _tool_calls(message):
                name, arguments = _tool_name_and_arguments(call)
                script_match = _SCRIPT_PATTERN.search(arguments)
                calls.append({
                    "id": str(call.get("id") or call.get("call_id") or ""),
                    "name": name,
                    "arguments": arguments,
                    "script": script_match.group(1) if script_match else None,
                })
    successful_tools = failed_tools = unknown_tools = 0
    successful_scripts = failed_scripts = unknown_scripts = 0
    error_signatures = set(interaction_errors)
    retry_attempts = recovered_errors = 0
    previous_by_signature: dict[str, bool | None] = {}
    for call in calls:
        result = results.get(call["id"])
        failed = _result_failed(result) if result else None
        if failed is True:
            failed_tools += 1
            error_signatures.add(f"tool:{call['name']}:{str(result.get('content') or '')[:180]}")
        elif failed is False:
            successful_tools += 1
        else:
            unknown_tools += 1
        signature = f"{call['name']}:{call['script'] or call['arguments'][:160]}"
        if previous_by_signature.get(signature) is True:
            retry_attempts += 1
            if failed is False:
                recovered_errors += 1
        previous_by_signature[signature] = failed
        if call["script"]:
            if failed is True:
                failed_scripts += 1
            elif failed is False:
                successful_scripts += 1
            else:
                unknown_scripts += 1
    tool_known = successful_tools + failed_tools
    script_known = successful_scripts + failed_scripts
    steps = _skill_steps(task_type)
    lower = evidence_text.casefold()
    step_results = [
        {"step_id": step_id, "completed": any(marker.casefold() in lower for marker in markers), "evidence_markers": list(markers)}
        for step_id, markers in steps
    ]
    completed_steps = sum(bool(step["completed"]) for step in step_results)
    source_fingerprint = hashlib.sha256(
        json.dumps(
            [(row.get("request_id"), row.get("status"), row.get("total_tokens")) for row in rows],
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    coverage_checks = [bool(rows), bool(calls) == bool(tool_known or unknown_tools), all(bool(row.get("request_id")) for row in rows)]
    return {
        "session_id": str(conversation.get("root_session_id") or conversation.get("session_id") or ""),
        "task_type": task_type,
        "task_type_source": task_source,
        "task_type_confidence": task_confidence,
        "agent": conversation.get("agent"),
        "model": (conversation.get("models") or [None])[0],
        "end_user": conversation.get("end_user"),
        "started_at": conversation.get("started_at"),
        "finished_at": conversation.get("finished_at"),
        "metrics": {
            "tool_call_count": len(calls),
            "tool_success_count": successful_tools,
            "tool_failure_count": failed_tools,
            "tool_unknown_count": unknown_tools,
            "tool_success_rate": round(successful_tools / tool_known * 100, 2) if tool_known else None,
            "skill_completeness": round(completed_steps / len(steps) * 100, 2) if steps else None,
            "skill_completed_steps": completed_steps,
            "skill_required_steps": len(steps),
            "error_count": len(error_signatures),
            "retry_attempt_count": retry_attempts,
            "recovered_error_count": recovered_errors,
            "unrecovered_error_count": max(0, len(error_signatures) - recovered_errors),
            "confirmed_fabrication_count": None,
            "suspected_fabrication_count": None,
            "script_call_count": successful_scripts + failed_scripts + unknown_scripts,
            "script_success_count": successful_scripts,
            "script_failure_count": failed_scripts,
            "script_unknown_count": unknown_scripts,
            "script_success_rate": round(successful_scripts / script_known * 100, 2) if script_known else None,
        },
        "skill_steps": step_results,
        "fabrication_status": "requires_llm_and_artifact_evidence",
        "evidence_coverage": round(sum(coverage_checks) / len(coverage_checks), 2),
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "source_fingerprint": source_fingerprint,
        "judge": {"status": "pending"},
        "status": "rules_completed",
        "calculated_at": datetime.now(timezone.utc),
    }
