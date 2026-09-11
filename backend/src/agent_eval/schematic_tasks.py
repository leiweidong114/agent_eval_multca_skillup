from __future__ import annotations

from typing import Any, Mapping

from agent_eval.skill_sources import MAX_SELECTED_SKILLS


SCHEMATIC_TASK_TYPES: dict[str, dict[str, str]] = {
    "block_to_schematic": {
        "name": "框图生成原理图",
        "input_contract": "block_diagram",
        "output_contract": "schematic_project",
    },
    "block_to_signal_list": {
        "name": "框图生成信号接口列表",
        "input_contract": "block_diagram",
        "output_contract": "signal_interface_v1",
    },
    "signal_list_to_schematic": {
        "name": "信号接口列表生成原理图",
        "input_contract": "signal_interface_v1",
        "output_contract": "schematic_project",
    },
}

DEFAULT_SCHEMATIC_TASK_TYPE = "block_to_schematic"
DEFAULT_PIPELINE_SKILLS = [
    "schematic-pipeline",
    "signal-interface-generation",
    "schematic-layout-codegen",
    "schematic-web-apply",
]
DEFAULT_SCHEMATIC_TASK_PROFILES: dict[str, dict[str, Any]] = {
    "block_to_schematic": {
        "skills": list(DEFAULT_PIPELINE_SKILLS),
        "evaluator_id": "schematic-default",
    },
    "block_to_signal_list": {
        "skills": ["signal-interface-generation"],
        "evaluator_id": "schematic-default",
    },
    "signal_list_to_schematic": {
        "skills": ["schematic-layout-codegen", "schematic-web-apply"],
        "evaluator_id": "schematic-default",
    },
}


def normalize_schematic_task_profiles(
    value: object,
    *,
    legacy_skills: list[str] | None = None,
    legacy_evaluator: str | None = None,
) -> dict[str, dict[str, Any]]:
    supplied = value if isinstance(value, Mapping) else {}
    profiles: dict[str, dict[str, Any]] = {}
    for task_type, defaults in DEFAULT_SCHEMATIC_TASK_PROFILES.items():
        raw = supplied.get(task_type)
        raw = raw if isinstance(raw, Mapping) else {}
        skills = raw.get("skills")
        if task_type == DEFAULT_SCHEMATIC_TASK_TYPE and not skills and legacy_skills:
            skills = legacy_skills
        if not isinstance(skills, list) or not skills:
            skills = defaults["skills"]
        normalized_skills = list(dict.fromkeys(
            str(item).strip() for item in skills if str(item).strip()
        ))[:MAX_SELECTED_SKILLS]
        evaluator_id = str(raw.get("evaluator_id") or "").strip()
        if task_type == DEFAULT_SCHEMATIC_TASK_TYPE and not evaluator_id and legacy_evaluator:
            evaluator_id = legacy_evaluator
        profiles[task_type] = {
            "skills": normalized_skills or list(defaults["skills"]),
            "evaluator_id": evaluator_id or str(defaults["evaluator_id"]),
        }
    return profiles


def list_schematic_task_types() -> list[dict[str, str]]:
    return [
        {"id": task_type, **metadata}
        for task_type, metadata in SCHEMATIC_TASK_TYPES.items()
    ]
