from agent_eval.skill_quality import evaluate_skill_quality


def test_skill_quality_reports_transparent_checks(tmp_path):
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        """---
name: demo
description: Generate a JSON artifact
---
# Workflow
Step 1: produce output JSON. Must validate it. Retry on error.
""",
        encoding="utf-8",
    )
    result = evaluate_skill_quality(skill)
    assert result["score"] == 100
    assert result["method"] == "deterministic_structure_v1"
    assert all(item["passed"] for item in result["details"])
