from __future__ import annotations

from typing import Any

from agent_eval.evaluators.protocol import (
    EVALUATOR_API_VERSION,
    EvaluationContext,
    EvaluationEvidence,
    PluginEvaluation,
)
from agent_eval.scoring import calculate_rule_dimensions
from .result_rules import evaluate_skill_result


class SkillDefaultEvaluator:
    """Artifact-aware evaluator for the project-bundled non-schematic Skills."""

    id = "skill-default"
    version = "1"
    api_version = EVALUATOR_API_VERSION
    evaluation_types = ("skill",)
    schematic_task_types: tuple[str, ...] = ()

    def evaluate(
        self,
        *,
        context: EvaluationContext,
        evidence: EvaluationEvidence,
        scoring_config: dict[str, Any],
    ) -> PluginEvaluation:
        dimensions = calculate_rule_dimensions(
            scores=evidence.deterministic_scores,
            process=evidence.process_metrics,
            skill_quality=evidence.skill_quality,
            config=scoring_config,
        )
        result = evaluate_skill_result(context.skill_name, evidence)
        dimensions["result"] = {"score": result["score"], "evidence": result}
        return PluginEvaluation(
            rule_dimensions=dimensions,
            llm_evidence={
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
                "deliverable_acceptance": result,
            },
            extensions={"skill": {"profile": self.id, "acceptance": result}},
        )


PLUGIN = SkillDefaultEvaluator()
