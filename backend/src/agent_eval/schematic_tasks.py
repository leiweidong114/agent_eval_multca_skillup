from __future__ import annotations

import re
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

DEFAULT_SCHEMATIC_REFERENCE_TASKS: list[dict[str, Any]] = [
    {
        "id": "stm32-minimum-board",
        "name": "STM32 最小系统原理图",
        "schematic_task_type": "block_to_schematic",
        "prompt": "生成 STM32F103C8T6 最小系统原理图，包含 5V 转 3.3V 电源、去耦、复位、8MHz 晶振、SWD 和电源指示灯。",
        "reference_answer": "应包含 STM32F103C8T6、3.3V 稳压与输入输出电容、每组电源脚去耦、BOOT 配置、NRST 复位、8MHz 晶振及负载电容、SWDIO/SWCLK 接口和限流电阻串联的电源指示灯，并完成 ERC/连通性检查。",
        "must_contain": ["STM32F103C8T6", "SWD", "8MHz", "3.3V"],
    },
    {
        "id": "protected-buck-supply",
        "name": "24V 转 5V 降压电源",
        "schematic_task_type": "block_to_schematic",
        "prompt": "生成 24V 输入、5V/3A 输出的降压电源原理图，包含输入保护、滤波、反馈、状态指示和测试点。",
        "reference_answer": "输入侧应包含保险或限流、反接保护、TVS 与滤波；降压芯片外围包含电感、续流/同步器件、输入输出电容和反馈分压；输出应有状态指示、GND/输入/输出测试点，并满足器件耐压、电流和功耗裕量。",
        "must_contain": ["24V", "5V", "3A", "TVS", "反馈"],
    },
    {
        "id": "rs485-interface-list",
        "name": "隔离 RS-485 信号接口列表",
        "schematic_task_type": "block_to_signal_list",
        "prompt": "为 MCU 与隔离 RS-485 收发器生成信号接口列表，覆盖 UART、方向控制、隔离电源、A/B 总线、终端和保护。",
        "reference_answer": "接口列表至少应包含 MCU_TX、MCU_RX、DE/RE、隔离侧与逻辑侧电源和地、RS485_A、RS485_B、120Ω 终端、偏置网络以及总线 TVS/浪涌保护，并标明方向和电气域。",
        "must_contain": ["MCU_TX", "MCU_RX", "RS485_A", "RS485_B", "120"],
    },
]


def normalize_schematic_reference_tasks(value: object) -> list[dict[str, Any]]:
    """Normalize editable golden tasks while keeping their persisted order."""
    supplied = value if isinstance(value, list) and value else DEFAULT_SCHEMATIC_REFERENCE_TASKS
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(supplied):
        if not isinstance(raw, Mapping):
            continue
        task_id = str(raw.get("id") or f"reference-task-{index + 1}").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", task_id) or task_id in seen:
            continue
        task_type = str(raw.get("schematic_task_type") or DEFAULT_SCHEMATIC_TASK_TYPE)
        if task_type not in SCHEMATIC_TASK_TYPES:
            task_type = DEFAULT_SCHEMATIC_TASK_TYPE
        prompt = str(raw.get("prompt") or "").strip()
        reference_answer = str(raw.get("reference_answer") or "").strip()
        if not prompt or not reference_answer:
            continue
        must_contain = raw.get("must_contain")
        must_contain = list(dict.fromkeys(
            str(item).strip() for item in must_contain if str(item).strip()
        )) if isinstance(must_contain, list) else []
        normalized.append({
            "id": task_id,
            "name": str(raw.get("name") or task_id).strip(),
            "schematic_task_type": task_type,
            "prompt": prompt,
            "reference_answer": reference_answer,
            "must_contain": must_contain,
        })
        seen.add(task_id)
    return normalized


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
