from __future__ import annotations

from typing import Any

from agent_eval.runtime import agent_capabilities


TELEMETRY_SCHEMA_VERSION = "agent-eval-telemetry-v1"

# A newly integrated Agent is not considered evaluation-ready until a live
# certification case proves model selection and the required normalized fields.
REQUIRED_AGENT_EVIDENCE = (
    "final_output",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "requested_model",
    "tool_events",
)


def describe_agent_contract(agent: str) -> dict[str, Any]:
    capabilities = agent_capabilities(agent)
    return {
        "agent": agent,
        "capabilities": capabilities,
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "required_for_certification": list(REQUIRED_AGENT_EVIDENCE),
        "normalized_channel": {
            "final_output": "required",
            "duration_ms": "required",
            "input_tokens": "required_but_provider_may_report_zero",
            "output_tokens": "required_but_provider_may_report_zero",
            "requested_model": "required",
            "actual_model": "conditional_on_agent_or_litellm_trace",
            "tool_calls": "conditional_on_agent_protocol_and_task",
            "tool_results": "conditional_on_agent_protocol_and_task",
            "subagent_calls": "best_effort_only",
            "context_tokens": "litellm_trace_only",
            "cache_tokens": "conditional_on_agent_protocol",
            "session_id": "conditional_on_agent_protocol",
        },
        "certification_policy": {
            "stable_invocation": "A live case must pass repeatedly with the selected model",
            "model_selection": (
                "Requested and observed model identities must match when observable"
                if capabilities["model_selection"]
                else "Unsupported: model is managed by the Agent runtime"
            ),
            "telemetry": "A dedicated probe must force at least one tool call and verify required evidence",
        },
    }


def assess_agent_contract(
    *,
    agent: str,
    requested_model: str,
    process_metrics: dict[str, Any],
    skill_up_exit_code: int,
) -> dict[str, Any]:
    observed_models = process_metrics.get("observed_models") or []
    fields = {
        "final_output": bool(process_metrics.get("final_output_present")),
        "duration_ms": process_metrics.get("total_duration_ms") is not None,
        "input_tokens": process_metrics.get("input_tokens") is not None,
        "output_tokens": process_metrics.get("output_tokens") is not None,
        "requested_model": bool(requested_model),
        # Zero tool calls is valid for an ordinary task, so this means the
        # normalized collector was active, not that a call necessarily occurred.
        "tool_events": process_metrics.get("tool_calls") is not None,
    }
    return {
        **describe_agent_contract(agent),
        "run_evidence": fields,
        "run_contract_passed": skill_up_exit_code == 0 and all(fields.values()),
        "requested_model": requested_model,
        "observed_models": observed_models,
        "model_identity_verified": requested_model in observed_models if observed_models else None,
        "note": "Full Agent certification requires a dedicated live model/tool probe; one ordinary run is not sufficient.",
    }
