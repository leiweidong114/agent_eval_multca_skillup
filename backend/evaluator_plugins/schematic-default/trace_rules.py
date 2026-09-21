from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from agent_eval.evaluators.protocol import EvaluationEvidence


REQUIRED_PIPELINE_SCRIPTS = (
    "fetch_catalog.py", "validate_sheets.py", "render_sheets_markdown.py",
    "codegen_base.py", "layout_sheet.py", "apply.py",
)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _tool_call_id(value: Any) -> str:
    """Normalize JustDo's OpenClaw `call_abc` / LiteLLM `callabc` IDs.

    The proxy removes the underscore after `call` in tool-result messages, while
    the model response and local transcript retain it. Limit this normalization
    to hex IDs so unrelated tool IDs cannot be accidentally paired.
    """
    call_id = str(value or "")
    match = re.fullmatch(r"call_([0-9a-f]{16,})", call_id, re.IGNORECASE)
    return f"call{match.group(1)}" if match else call_id


def _executed_scripts(name: str, arguments: Any) -> set[str]:
    """Count only scripts actually invoked by a shell tool, not plans or prose."""
    if name.lower() not in {"exec", "exec_command", "shell_command", "bash", "shell", "powershell"}:
        return set()
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            return set()
    if not isinstance(arguments, dict):
        return set()
    command = arguments.get("command") or arguments.get("cmd")
    if not isinstance(command, str):
        return set()
    return {
        script for script in REQUIRED_PIPELINE_SCRIPTS
        if re.search(
            rf"\b(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?\b[^\n;|]{{0,400}}\b{re.escape(script)}\b",
            command,
            re.IGNORECASE,
        )
    }


def _transcript_events(value: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            events.extend(_transcript_events(item))
    elif isinstance(value, dict):
        transcript = value.get("transcript")
        if isinstance(transcript, list):
            events.extend(item for item in transcript if isinstance(item, dict))
        for key, item in value.items():
            if key != "transcript":
                events.extend(_transcript_events(item))
    return events


def _interaction_events(interactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover tool calls/results from persisted LiteLLM request/response rows."""
    events: list[dict[str, Any]] = []
    for interaction in interactions:
        response = interaction.get("response") or {}
        choices = response.get("choices") if isinstance(response, dict) else []
        if isinstance(choices, list):
            for choice in choices:
                message = choice.get("message") if isinstance(choice, dict) else None
                tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
                if not isinstance(tool_calls, list):
                    continue
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function") or {}
                    if not isinstance(function, dict):
                        function = {}
                    events.append({
                        "role": "tool_call",
                        "tool_call": {
                            "id": tool_call.get("id"),
                            "name": function.get("name") or tool_call.get("name"),
                            "arguments": function.get("arguments") or tool_call.get("arguments"),
                        },
                    })
        proxy_request = interaction.get("proxy_server_request") or {}
        messages = proxy_request.get("messages") if isinstance(proxy_request, dict) else None
        if not isinstance(messages, list):
            messages = interaction.get("messages")
        if not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, dict) or str(message.get("role") or "").lower() != "tool":
                continue
            events.append({
                "role": "tool_result",
                "tool_result": {
                    "call_id": message.get("tool_call_id") or message.get("call_id"),
                    "status": "completed",
                    "content": message.get("content"),
                },
            })
    return events


def evaluate_trace(evidence: EvaluationEvidence) -> dict[str, object]:
    """Build deterministic, auditable assertions for every pipeline stage."""
    events = [
        *_transcript_events(evidence.results),
        *_interaction_events(evidence.interactions),
    ]
    calls: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events):
        role = str(event.get("role") or "").lower()
        if role == "tool_call" and isinstance(event.get("tool_call"), dict):
            call = event["tool_call"]
            call_id = _tool_call_id(call.get("id") or f"call-{index}")
            calls[call_id] = {
                "id": call_id,
                "name": str(call.get("name") or ""),
                "text": _text(call.get("arguments") or {}).lower().replace("\\", "/"),
                "executed_scripts": _executed_scripts(
                    str(call.get("name") or ""), call.get("arguments") or {}
                ),
            }
        elif role == "tool_result" and isinstance(event.get("tool_result"), dict):
            result = event["tool_result"]
            call_id = _tool_call_id(result.get("call_id") or result.get("tool_call_id") or "")
            if call_id:
                results[call_id] = result

    def result_ok(call_id: str) -> bool:
        result = results.get(call_id)
        if not result:
            return False
        status = str(result.get("status") or "completed").lower()
        body = _text(result.get("content") or result.get("output") or "").lower()
        explicit_failure = any(
            marker in body for marker in (
                '"status": "error"', '"status":"error"',
                "traceback (most recent call last)", "timed out",
                "校验失败", "找不到路径",
            )
        ) or bool(
            re.search(r"\bexit\s+code\s*:\s*[1-9]\d*\b", body)
            or re.search(r'"exit_code"\s*:\s*[1-9]\d*\b', body)
            or re.search(r"\bprocess exited with (?:status|code)\s+[1-9]\d*\b", body)
        )
        return status in {"completed", "success", "ok"} and not explicit_failure

    script_evidence = []
    for script in REQUIRED_PIPELINE_SCRIPTS:
        ids = [call_id for call_id, call in calls.items() if script in call["executed_scripts"]]
        successful_ids = [call_id for call_id in ids if result_ok(call_id)]
        script_evidence.append({
            "script": script, "attempts": len(ids),
            "successful_calls": len(successful_ids), "passed": bool(successful_ids),
            "call_ids": ids,
        })

    paths = [str(item.get("path") or "").replace("\\", "/").lower() for item in evidence.artifact_manifest]
    pipeline_artifact_paths = sorted({
        path for path in paths
        if "/with_skill/outputs/workspace/out/" in f"/{path}"
    })
    artifact_assertions = {
        "catalog_generated": any(path.endswith("/out/catalog.json") for path in paths),
        "signal_list_generated": any(path.endswith("/out/sheets.json") for path in paths),
        "signal_list_rendered": any("/out/sheets_markdown/" in path for path in paths),
        "codegen_slices_generated": any("/out/frags/" in path and path.endswith(("/base.txt", "/slices.json")) for path in paths),
        "layout_generated": any("/out/layout/" in path and path.endswith(".json") for path in paths),
        "web_apply_generated": any(path.endswith("/out/apply_result.json") for path in paths),
    }
    artifact_labels = {
        "catalog_generated": "器件目录产物已生成", "signal_list_generated": "信号接口列表已生成",
        "signal_list_rendered": "信号接口可读文档已生成", "codegen_slices_generated": "器件切片与代码生成基线已生成",
        "layout_generated": "原理图布局 JSON 已生成", "web_apply_generated": "网页应用结果已生成",
    }
    assertions = [{
        "name": "all_selected_skills_observed", "label": "四个 Skill 均有结构化调用证据",
        "passed": bool(evidence.skill_usage.get("all_selected_skills_observed")),
        "evidence": evidence.skill_usage.get("observed_skills") or [],
    }]
    assertions.extend({
        "name": f"script_{item['script'].removesuffix('.py')}", "label": f"脚本 {item['script']} 执行成功",
        "passed": item["passed"], "evidence": {"attempts": item["attempts"], "successful_calls": item["successful_calls"], "call_ids": item["call_ids"]},
    } for item in script_evidence)
    assertions.append({
        "name": "subagent_completed", "label": "Subagent 生成切片并成功返回",
        "passed": int(evidence.process_metrics.get("subagent_calls") or 0) > 0,
        "evidence": {"attempts": evidence.process_metrics.get("subagent_attempts"), "completed": evidence.process_metrics.get("subagent_calls"), "failures": evidence.process_metrics.get("subagent_failures")},
    })
    assertions.extend({"name": name, "label": artifact_labels[name], "passed": passed, "evidence": "artifact_manifest"} for name, passed in artifact_assertions.items())
    passed = sum(bool(item["passed"]) for item in assertions)
    attempts = sum(item["attempts"] for item in script_evidence)
    successes = sum(item["successful_calls"] for item in script_evidence)
    correlated_call_ids = [call_id for call_id in calls if call_id in results]
    semantic_tool_successes = sum(result_ok(call_id) for call_id in correlated_call_ids)
    semantic_tool_failures = len(correlated_call_ids) - semantic_tool_successes
    return {
        "schema_version": "schematic-trace-v2", "assertions": assertions,
        "assertion_total": len(assertions), "assertion_passed": passed,
        "assertion_completion_rate": round(100 * passed / len(assertions), 2) if assertions else None,
        "tool_calls": evidence.process_metrics.get("tool_calls"),
        "tool_completion_rate": round(100 * semantic_tool_successes / len(correlated_call_ids), 2) if correlated_call_ids else None,
        "tool_successes": semantic_tool_successes,
        "tool_failures": semantic_tool_failures,
        "transport_tool_completion_rate": evidence.process_metrics.get("tool_completion_rate"),
        "transport_tool_failures": evidence.process_metrics.get("tool_failures"),
        "tool_name_counts": dict(Counter(call["name"] or "unknown" for call in calls.values())),
        "script_calls": attempts, "script_successes": successes,
        "script_success_rate": round(100 * successes / attempts, 2) if attempts else None,
        "script_retry_count": sum(max(0, item["attempts"] - 1) for item in script_evidence),
        "structured_tool_result_failures": semantic_tool_failures,
        "subagent_attempts": evidence.process_metrics.get("subagent_attempts"), "subagent_calls": evidence.process_metrics.get("subagent_calls"),
        "subagent_failures": evidence.process_metrics.get("subagent_failures"), "error_event_count": evidence.process_metrics.get("error_event_count"),
        "model_interaction_count": len(evidence.interactions), "model_call_success_rate": evidence.process_metrics.get("model_call_success_rate"),
        "total_tokens": evidence.process_metrics.get("total_tokens"), "total_duration_ms": evidence.process_metrics.get("total_duration_ms"),
        "artifact_count": len(pipeline_artifact_paths),
        "workspace_file_count": len(evidence.artifact_manifest),
        "artifact_paths": pipeline_artifact_paths,
        "script_evidence": script_evidence,
    }
