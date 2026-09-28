from __future__ import annotations

from typing import Any, Mapping

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
DEFAULT_BLOCK_TO_SCHEMATIC_PROMPT = (
    "设计一块基于 STM32F103C8T6 的最小控制板原理图。要求包含：5V 输入与 3.3V 稳压、"
    "电源指示灯、SWD 下载接口、8MHz 晶振与负载电容、复位按键，以及由 GPIO 驱动的红色 "
    "LED（串联 470Ω 电阻）。请严格执行已安装的四阶段原理图 pipeline，使用器件目录中的器件；"
    "生成并校验 out/sheets.json，按每批 2 个 subagent 完成切片代码和自动布局，确保 "
    "overlap=0、unrouted=0，最后生成多图页网页并在结论中列出 URL、全部中间产物路径和每页指标。"
)
DEFAULT_SCHEMATIC_TASK_PROFILES: dict[str, dict[str, Any]] = {
    "block_to_schematic": {
        "skills": list(DEFAULT_PIPELINE_SKILLS),
        "evaluator_id": "schematic-default",
        "preset_prompt": DEFAULT_BLOCK_TO_SCHEMATIC_PROMPT,
    },
    "block_to_signal_list": {
        "skills": ["signal-interface-generation"],
        "evaluator_id": "schematic-default",
        "preset_prompt": "",
    },
    "signal_list_to_schematic": {
        "skills": ["schematic-layout-codegen", "schematic-web-apply"],
        "evaluator_id": "schematic-default",
        "preset_prompt": "",
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
        ))
        evaluator_id = str(raw.get("evaluator_id") or "").strip()
        if task_type == DEFAULT_SCHEMATIC_TASK_TYPE and not evaluator_id and legacy_evaluator:
            evaluator_id = legacy_evaluator
        raw_prompt = (
            raw.get("preset_prompt")
            if "preset_prompt" in raw
            else defaults.get("preset_prompt", "")
        )
        preset_prompt = str(raw_prompt or "").strip()
        profiles[task_type] = {
            "skills": normalized_skills or list(defaults["skills"]),
            "evaluator_id": evaluator_id or str(defaults["evaluator_id"]),
            "preset_prompt": preset_prompt,
        }
    return profiles


def list_schematic_task_types() -> list[dict[str, str]]:
    return [
        {"id": task_type, **metadata}
        for task_type, metadata in SCHEMATIC_TASK_TYPES.items()
    ]
