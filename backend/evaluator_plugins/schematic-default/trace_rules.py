from agent_eval.evaluators.protocol import EvaluationEvidence


def evaluate_trace(evidence: EvaluationEvidence) -> dict[str, object]:
    """Expose the stable process evidence used by the public default plugin."""
    return {
        "tool_calls": evidence.process_metrics.get("tool_calls"),
        "tool_completion_rate": evidence.process_metrics.get("tool_completion_rate"),
        "error_event_count": evidence.process_metrics.get("error_event_count"),
        "model_interaction_count": len(evidence.interactions),
    }
