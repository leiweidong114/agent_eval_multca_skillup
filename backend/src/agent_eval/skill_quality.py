from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


def evaluate_skill_quality(skill_dir: Path) -> dict[str, Any]:
    """Return a transparent structural score for a Skill, without an LLM judge."""
    path = skill_dir / "SKILL.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    frontmatter: dict[str, Any] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            parsed = yaml.safe_load(parts[1]) or {}
            if isinstance(parsed, dict):
                frontmatter = parsed

    checks = [
        ("skill_md", 20, bool(text.strip()), "SKILL.md exists and is not empty"),
        ("name", 10, bool(str(frontmatter.get("name") or "").strip()), "frontmatter has name"),
        ("description", 10, bool(str(frontmatter.get("description") or "").strip()), "frontmatter has description"),
        ("workflow", 15, bool(re.search(r"步骤|流程|workflow|step", text, re.I)), "workflow or steps are described"),
        ("constraints", 15, bool(re.search(r"必须|禁止|约束|must|never|constraint", text, re.I)), "constraints are explicit"),
        ("output_contract", 15, bool(re.search(r"输出|产物|json|artifact|output", text, re.I)), "output/artifact contract is described"),
        ("error_handling", 7.5, bool(re.search(r"失败|错误|重试|异常|error|retry|fail", text, re.I)), "failure handling is described"),
        ("verification", 7.5, bool(re.search(r"验证|检查|测试|verify|validate|test", text, re.I)), "verification is described"),
    ]
    details = [
        {"check": name, "weight": weight, "passed": passed, "description": description}
        for name, weight, passed, description in checks
    ]
    return {
        "score": round(sum(weight for _, weight, passed, _ in checks if passed), 2),
        "method": "deterministic_structure_v1",
        "details": details,
        "character_count": len(text),
    }
