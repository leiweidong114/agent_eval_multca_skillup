from __future__ import annotations

from typing import Any

from agent_eval.evaluators.protocol import (
    EVALUATOR_API_VERSION,
    EvaluationContext,
    EvaluationEvidence,
    PluginEvaluation,
)
from agent_eval.scoring import calculate_rule_dimensions


class DefaultEvaluator:
    """Compatibility evaluator that preserves the original three-dimension scoring."""

    api_version = EVALUATOR_API_VERSION
    version = "1"

    def __init__(self, evaluator_id: str, evaluation_types: tuple[str, ...]) -> None:
        self.id = evaluator_id
        self.evaluation_types = evaluation_types

    def evaluate(
        self,
        *,
        context: EvaluationContext,
        evidence: EvaluationEvidence,
        scoring_config: dict[str, Any],
    ) -> PluginEvaluation:
        rule_dimensions = calculate_rule_dimensions(
            scores=evidence.deterministic_scores,
            process=evidence.process_metrics,
            skill_quality=evidence.skill_quality,
            config=scoring_config,
        )
        llm_evidence = {
            "task": {
                "task_id": context.task_id,
                "agent": context.agent,
                "requested_model": context.requested_model,
                "skill": context.skill_name,
                "skills": list(context.selected_skills),
                "evaluation_type": context.evaluation_type,
            },
            "deterministic_scores": evidence.deterministic_scores,
            "process_metrics": evidence.process_metrics,
            "skill_usage": evidence.skill_usage,
            "skill_quality_rules": evidence.skill_quality,
            "skill_md": context.skill_md,
            "skill_up_results": evidence.results,
        }
        return PluginEvaluation(
            rule_dimensions=rule_dimensions,
            llm_evidence=llm_evidence,
        )


GENERIC_EVALUATOR = DefaultEvaluator("generic", ("skill", "schematic"))
SCHEMATIC_DEFAULT_EVALUATOR = DefaultEvaluator("schematic-default", ("schematic",))
