from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "dimensions": {
        "result": {"weight": 0.50, "rule_weight": 0.65, "llm_weight": 0.35},
        "process": {"weight": 0.30, "rule_weight": 0.60, "llm_weight": 0.40},
        "skill_quality": {"weight": 0.20, "rule_weight": 0.55, "llm_weight": 0.45},
    },
    "process_rules": {
        "execution_stability_weight": 0.45,
        "model_success_weight": 0.20,
        "tool_completion_weight": 0.20,
        "error_free_weight": 0.15,
    },
    "llm_judge": {"enabled": False, "required": False},
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_scoring_config(project_root: Path) -> dict[str, Any]:
    path = project_root / "config" / "scoring.yaml"
    configured = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    configured = configured if isinstance(configured, dict) else {}
    local_path = project_root / "config" / "local.yaml"
    local = yaml.safe_load(local_path.read_text(encoding="utf-8")) if local_path.is_file() else {}
    local_scoring = (local or {}).get("scoring") if isinstance(local, dict) else {}
    return _merge(_merge(DEFAULT_CONFIG, configured), local_scoring or {})


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _event_from_message(message: dict[str, Any]) -> dict[str, Any] | None:
    role = str(message.get("role") or "")
    if role == "tool_call":
        call = message.get("tool_call") or {}
        return {
            "type": "tool-use", "tool": call.get("name"), "call_id": call.get("id"),
            "arguments": call.get("arguments"),
        }
    if role == "tool_result":
        result = message.get("tool_result") or {}
        return {
            "type": "tool-result", "call_id": result.get("call_id"),
            "status": result.get("status"), "duration_ms": result.get("duration_ms"),
        }
    if role == "error":
        return {"type": "error", "content": message.get("content")}
    content = message.get("content")
    if not isinstance(content, str):
        return None
    marker = "AGENT_EVAL_TELEMETRY_JSON:"
    if content.startswith(marker):
        try:
            return {"type": "telemetry-summary", **json.loads(content[len(marker):])}
        except json.JSONDecodeError:
            return None
    if content.startswith("{"):
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) and parsed.get("type") else None
        except json.JSONDecodeError:
            return None
    return None


def _is_subagent_spawn_tool(value: Any, arguments: Any = None) -> bool:
    name = str(value or "").lower().replace("-", "_")
    base_name = name.rsplit("__", 1)[-1]
    if base_name in {
        "sessions_spawn", "spawn_agent", "subagent_spawn", "start_subagent", "agent", "task",
    }:
        return True
    if base_name != "exec":
        return False
    serialized = (
        arguments if isinstance(arguments, str)
        else json.dumps(arguments, ensure_ascii=False, default=str)
    )
    return (
        "openclaw agent exec" in serialized
        or "agent-eval check-agent --agent justdo" in serialized
    )


def collect_process_metrics(
    results: list[dict[str, Any]], database_trace: dict[str, Any]
) -> dict[str, Any]:
    tool_calls = tool_results = tool_failures = errors = 0
    assistant_messages = thinking_events = 0
    subagent_call_ids: set[str] = set()
    successful_tool_result_ids: set[str] = set()
    final_output_present = False
    input_tokens = output_tokens = cache_read_tokens = cache_write_tokens = 0
    observed_models: set[str] = set()
    durations: list[int] = []
    seen_messages: set[int] = set()

    for item in _walk(results):
        if not isinstance(item, dict):
            continue
        if str(item.get("final_message") or "").strip():
            final_output_present = True
        if "duration_ms" in item and isinstance(item.get("duration_ms"), (int, float)):
            durations.append(int(item["duration_ms"]))
        if "transcript" in item and isinstance(item["transcript"], list):
            for message in item["transcript"]:
                if not isinstance(message, dict) or id(message) in seen_messages:
                    continue
                seen_messages.add(id(message))
                role = str(message.get("role") or "")
                event = _event_from_message(message)
                if role == "assistant" and not (
                    event and event.get("type") == "telemetry-summary"
                ):
                    assistant_messages += 1
                if not event:
                    continue
                event_type = str(event.get("type") or "")
                if event_type in {"tool-use", "tool_call"}:
                    tool_calls += 1
                    tool = str(event.get("tool") or "").lower()
                    if _is_subagent_spawn_tool(tool, event.get("arguments")):
                        subagent_call_ids.add(str(event.get("call_id") or f"anonymous-{tool_calls}"))
                elif event_type in {"tool-result", "tool_result"}:
                    tool_results += 1
                    result_status = str(event.get("status") or "").lower()
                    if result_status in {"failed", "error", "forbidden"}:
                        tool_failures += 1
                    else:
                        successful_tool_result_ids.add(str(event.get("call_id") or ""))
                elif event_type == "error":
                    errors += 1
                elif event_type == "thinking":
                    thinking_events += 1
                elif event_type == "telemetry-summary":
                    input_tokens += int(event.get("input_tokens") or 0)
                    output_tokens += int(event.get("output_tokens") or 0)
                    cache_read_tokens += int(event.get("cache_read_tokens") or 0)
                    cache_write_tokens += int(event.get("cache_write_tokens") or 0)
                    for model in event.get("models") or []:
                        if model:
                            observed_models.add(str(model))

    # LiteLLM is the most reliable cross-Agent source when all Agents share the
    # same gateway. Prefer its aggregate token counts if present.
    if database_trace.get("model_call_count"):
        input_tokens = int(database_trace.get("prompt_tokens") or input_tokens)
        output_tokens = int(database_trace.get("completion_tokens") or output_tokens)
        for model in database_trace.get("models") or []:
            observed_models.add(str(model))

    completed_tools = max(0, tool_results - tool_failures)
    return {
        "schema_version": "agent-eval-process-v1",
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "tool_failures": tool_failures,
        "tool_completion_rate": round(100 * completed_tools / tool_calls, 2) if tool_calls else None,
        "subagent_calls": len(subagent_call_ids & successful_tool_result_ids),
        "subagent_attempts": len(subagent_call_ids),
        "subagent_failures": len(subagent_call_ids - successful_tool_result_ids),
        "subagent_detection": "successful_tool_result_correlation",
        "assistant_message_count": assistant_messages,
        "final_output_present": final_output_present,
        "thinking_event_count": thinking_events,
        "error_event_count": errors,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "total_tokens": input_tokens + output_tokens + cache_read_tokens + cache_write_tokens,
        # Nested Skill-Up reports may repeat duration fields. The runner
        # replaces this best-effort value with aggregate_scores' case total.
        "total_duration_ms": sum(durations) if durations else 0,
        "max_context_tokens": database_trace.get("max_prompt_tokens"),
        "context_measurement": "litellm_prompt_tokens" if database_trace.get("max_prompt_tokens") is not None else "unavailable",
        "model_call_count": database_trace.get("model_call_count"),
        "model_call_success_rate": database_trace.get("model_call_success_rate"),
        "observed_models": sorted(observed_models),
    }


def _weighted_available(items: list[tuple[float | None, float]]) -> float | None:
    present = [(value, weight) for value, weight in items if value is not None and weight > 0]
    if not present:
        return None
    total = sum(weight for _, weight in present)
    return round(sum(float(value) * weight for value, weight in present) / total, 2)


def supplement_database_tool_metrics(process: dict[str, Any], interactions: list[dict[str, Any]]) -> None:
    """Fallback for non-streaming CLIs. Count observed protocol events, not guesses.

    Calls are deduplicated across repeated conversation histories by call ID.
    Missing results remain missing. This is run-level, not per-case telemetry.
    """
    if process.get('tool_calls'):
        process['tool_event_source'] = 'agent_transcript'
        return
    def obj(value):
        if isinstance(value, str):
            try: return json.loads(value)
            except ValueError: return {}
        return value or {}
    calls, results, response_calls = {}, {}, {}

    def result_failed(message: dict[str, Any]) -> bool:
        content = message.get('content')
        blocks = content if isinstance(content, list) else [content]
        for block in blocks:
            text = block.get('text') if isinstance(block, dict) else block
            if not isinstance(text, str):
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                status = str(payload.get('status') or '').lower()
                if payload.get('isError') is True or status in {'error', 'failed', 'forbidden'}:
                    return True
            lowered = text.lower()
            if '"status": "error"' in lowered or '"status": "forbidden"' in lowered:
                return True
        return False
    for row in interactions:
        request = obj(row.get('proxy_server_request'))
        request = request if isinstance(request, dict) else {}
        request = obj(request.get('body')) or request
        messages = obj(request.get('messages') or row.get('messages'))
        response = obj(row.get('response'))
        response = response if isinstance(response, dict) else {}
        messages = messages if isinstance(messages, list) else []
        for choice in response.get('choices') or []:
            for call in (choice.get('message') or {}).get('tool_calls') or []:
                function = call.get('function') or {}
                if call.get('id'):
                    response_calls[call['id']] = (function.get('name', ''), function.get('arguments'))
        for message in messages:
            for call in message.get('tool_calls') or []:
                function = call.get('function') or {}
                if call.get('id'):
                    calls[call['id']] = (function.get('name', ''), function.get('arguments'))
            if message.get('role') == 'tool' and message.get('tool_call_id'):
                results[message['tool_call_id']] = message
    for cid, call_info in response_calls.items():
        name, _ = call_info
        # OpenClaw sanitizes upstream call IDs (e.g. call_abc -> callabc).
        # Join only a unique same-name match, preserving ambiguous IDs.
        matches = [
            key for key, tool_info in calls.items()
            if tool_info[0] == name
            and re.sub(r'[^a-zA-Z0-9]', '', key) == re.sub(r'[^a-zA-Z0-9]', '', cid)
        ]
        if cid not in calls and len(matches) != 1:
            calls[cid] = call_info
    process['tool_event_source'] = 'litellm_conversation_fallback' if calls else 'not_observed'
    if calls:
        matched = set(results) & set(calls)
        failed = {cid for cid in matched if result_failed(results[cid])}
        completed = len(matched - failed)
        subagent_ids = {
            cid for cid, (name, arguments) in calls.items()
            if _is_subagent_spawn_tool(name, arguments)
        }
        process.update(tool_calls=len(calls), tool_results=completed,
                       tool_completion_rate=round(100*completed/len(calls), 2),
                       tool_failures=len(failed),
                       subagent_calls=len((subagent_ids & matched) - failed),
                       subagent_attempts=len(subagent_ids),
                       subagent_failures=len(subagent_ids - ((subagent_ids & matched) - failed)),
                       subagent_detection='successful_tool_result_correlation',
                       tool_failure_measurement='tool_result_content')


def collect_skill_read_evidence(
    interactions: list[dict[str, Any]], selected_skills: list[str]
) -> dict[str, Any]:
    """Prove explicit SKILL.md reads from model-emitted read-like tool calls."""
    observed: dict[str, list[dict[str, str]]] = {name: [] for name in selected_skills}

    def obj(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value

    def visit(node: Any, request_id: str) -> None:
        node = obj(node)
        if isinstance(node, list):
            for item in node:
                visit(item, request_id)
            return
        if not isinstance(node, dict):
            return
        for call in node.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else call
            tool = str(function.get("name") or call.get("name") or "").lower()
            arguments = obj(function.get("arguments") or call.get("arguments") or {})
            argument_text = json.dumps(arguments, ensure_ascii=False, default=str).lower()
            if any(marker in tool for marker in ("read", "open", "view")) and "skill.md" in argument_text:
                for skill in selected_skills:
                    if skill.lower() in argument_text:
                        observed[skill].append({"request_id": request_id, "tool": tool})
        for key, child in node.items():
            if key != "tool_calls":
                visit(child, request_id)

    for row in interactions:
        request_id = str(row.get("request_id") or "")
        visit(row.get("proxy_server_request") or row.get("messages"), request_id)
        visit(row.get("response"), request_id)
    missing = [skill for skill, evidence in observed.items() if not evidence]
    return {
        "status": "verified" if not missing else "partial" if len(missing) < len(selected_skills) else "not_observed",
        "all_selected_skills_read": not missing,
        "selected_skills": selected_skills,
        "observed_skills": [skill for skill, evidence in observed.items() if evidence],
        "missing_skills": missing,
        "evidence": observed,
        "method": "explicit_read_tool_call_with_skill_md_path",
    }


def calculate_rule_dimensions(
    *, scores: dict[str, Any], process: dict[str, Any], skill_quality: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    task = scores.get("task_score")
    gain = scores.get("skill_gain")
    lift_score = max(0.0, min(100.0, 50.0 + float(gain) / 2)) if gain is not None else None
    result_score = _weighted_available([(task, 0.80), (lift_score, 0.20)])

    process_config = config.get("process_rules") or {}
    error_free = max(0.0, 100.0 - 25.0 * int(process.get("error_event_count") or 0))
    process_score = _weighted_available([
        (scores.get("execution_stability"), float(process_config.get("execution_stability_weight", 0.45))),
        (process.get("model_call_success_rate"), float(process_config.get("model_success_weight", 0.20))),
        (process.get("tool_completion_rate"), float(process_config.get("tool_completion_weight", 0.20))),
        (error_free, float(process_config.get("error_free_weight", 0.15))),
    ])
    return {
        "result": {"score": result_score, "evidence": {"task_score": task, "skill_lift_score": lift_score}},
        "process": {"score": process_score, "evidence": process},
        "skill_quality": {"score": skill_quality.get("score"), "evidence": skill_quality.get("details")},
    }


def combine_dimensions(
    *, rule_dimensions: dict[str, Any], llm_judge: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    dimensions: dict[str, Any] = {}
    overall_parts: list[tuple[float | None, float]] = []
    llm_dimensions = llm_judge.get("dimensions") if llm_judge.get("status") == "completed" else {}
    llm_dimensions = llm_dimensions if isinstance(llm_dimensions, dict) else {}
    for name in ("result", "process", "skill_quality"):
        dimension_config = (config.get("dimensions") or {}).get(name) or {}
        rule = rule_dimensions.get(name) or {}
        llm = llm_dimensions.get(name) or {}
        combined = _weighted_available([
            (rule.get("score"), float(dimension_config.get("rule_weight", 1))),
            (llm.get("score"), float(dimension_config.get("llm_weight", 0))),
        ])
        dimensions[name] = {
            "score": combined,
            "rule": rule,
            "llm": llm or None,
            "weights": {
                "dimension": float(dimension_config.get("weight", 0)),
                "rule": float(dimension_config.get("rule_weight", 1)),
                "llm": float(dimension_config.get("llm_weight", 0)),
            },
        }
        overall_parts.append((combined, float(dimension_config.get("weight", 0))))
    return {
        "schema_version": "agent-eval-scoring-v1",
        "overall_score": _weighted_available(overall_parts),
        "dimensions": dimensions,
        "llm_judge": llm_judge,
    }
