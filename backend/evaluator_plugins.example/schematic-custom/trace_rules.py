from agent_eval.evaluators.protocol import EvaluationEvidence


def evaluate_trace(evidence: EvaluationEvidence) -> dict[str, object]:
    """Replace with deterministic private checks over normalized trace evidence."""
    return {
        "tool_calls": evidence.process_metrics.get("tool_calls"),
        "tool_completion_rate": evidence.process_metrics.get("tool_completion_rate"),
        "model_interaction_count": len(evidence.interactions),
    }
