from __future__ import annotations

from typing import Any

from agent_eval.evaluators.protocol import (
    EVALUATOR_API_VERSION,
    EvaluationContext,
    EvaluationEvidence,
    PluginEvaluation,
)
from agent_eval.scoring import calculate_rule_dimensions
from .judge_prompt import SYSTEM_PROMPT
from .result_rules import evaluate_result
from .trace_rules import evaluate_trace


class SchematicDefaultEvaluator:
    """Public default plugin shared by all supported schematic task types."""

    id = "schematic-default"
    version = "2"
    api_version = EVALUATOR_API_VERSION
    evaluation_types = ("schematic",)
    schematic_task_types = (
        "block_to_schematic",
        "block_to_signal_list",
        "signal_list_to_schematic",
    )

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
        result_evidence = evaluate_result(
            evidence, task_type=context.schematic_task_type or "block_to_schematic"
        )
        dimensions["result"] = {
            "score": result_evidence["score"],
            "evidence": result_evidence,
        }
        trace_evidence = evaluate_trace(evidence)
        judge_evidence = {
            "task": {
                "task_id": context.task_id,
                "agent": context.agent,
                "requested_model": context.requested_model,
                "skill": context.skill_name,
                "skills": list(context.selected_skills),
                "evaluation_type": context.evaluation_type,
                "schematic_task_type": context.schematic_task_type,
            },
            "deterministic_scores": evidence.deterministic_scores,
            "process_metrics": evidence.process_metrics,
            "skill_usage": evidence.skill_usage,
            "skill_quality_rules": evidence.skill_quality,
            "skill_md": context.skill_md,
            "skill_up_results": evidence.results,
            "schematic_result": result_evidence,
        }
        return PluginEvaluation(
            rule_dimensions=dimensions,
            llm_evidence=judge_evidence,
            judge_system_prompt=SYSTEM_PROMPT,
            extensions={
                "schematic": {
                    "profile": self.id,
                    "trace": trace_evidence,
                    "result": result_evidence,
                    "acceptance": result_evidence,
                }
            },
        )


PLUGIN = SchematicDefaultEvaluator()
