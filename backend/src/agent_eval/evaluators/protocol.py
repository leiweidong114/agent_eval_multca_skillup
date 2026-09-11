from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


EVALUATOR_API_VERSION = "agent-eval-evaluator-v1"


@dataclass(frozen=True)
class EvaluationContext:
    run_id: str
    task_id: str
    evaluation_type: str
    agent: str
    requested_model: str
    skill_name: str
    selected_skills: tuple[str, ...]
    skill_md: str
    schematic_task_type: str | None = None


@dataclass(frozen=True)
class EvaluationEvidence:
    deterministic_scores: dict[str, Any]
    process_metrics: dict[str, Any]
    skill_usage: dict[str, Any]
    skill_quality: dict[str, Any]
    results: list[dict[str, Any]]
    interactions: list[dict[str, Any]]


@dataclass(frozen=True)
class PluginEvaluation:
    rule_dimensions: dict[str, Any]
    llm_evidence: dict[str, Any]
    judge_system_prompt: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class EvaluationPlugin(Protocol):
    id: str
    version: str
    api_version: str
    evaluation_types: tuple[str, ...]
    schematic_task_types: tuple[str, ...]

    def evaluate(
        self,
        *,
        context: EvaluationContext,
        evidence: EvaluationEvidence,
        scoring_config: dict[str, Any],
    ) -> PluginEvaluation: ...
