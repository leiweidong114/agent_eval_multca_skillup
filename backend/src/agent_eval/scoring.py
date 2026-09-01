from __future__ import annotations

import json
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
        return {"type": "tool-use", "tool": call.get("name"), "call_id": call.get("id")}
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


def collect_process_metrics(
    results: list[dict[str, Any]], database_trace: dict[str, Any]
) -> dict[str, Any]:
    tool_calls = tool_results = tool_failures = errors = 0
    assistant_messages = thinking_events = subagent_calls = 0
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
                    if "subagent" in tool or "spawn_agent" in tool:
                        subagent_calls += 1
                elif event_type in {"tool-result", "tool_result"}:
                    tool_results += 1
                    if str(event.get("status") or "").lower() in {"failed", "error"}:
                        tool_failures += 1
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
        "subagent_calls": subagent_calls,
        "subagent_detection": "best_effort_tool_name_heuristic",
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
