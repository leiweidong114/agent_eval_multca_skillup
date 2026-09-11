from agent_eval.evaluators.protocol import EvaluationEvidence


def evaluate_result(evidence: EvaluationEvidence) -> dict[str, object]:
    """Expose generic result evidence without imposing private schematic rules."""
    return {
        "iteration_count": len(evidence.results),
        "task_score": evidence.deterministic_scores.get("task_score"),
        "artifact_count": len(evidence.artifact_manifest),
    }
