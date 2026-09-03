import argparse
import json

from agent_eval import cli
from agent_eval.cli_catalog import compose_skill_bundle, list_results, list_skills


def _skill(root, name, description="test skill"):
    path = root / "skills" / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n",
        encoding="utf-8",
    )
    return path


def test_lists_skills_and_pipeline_filter(tmp_path):
    _skill(tmp_path, "ordinary")
    for name in cli.SCHEMATIC_PIPELINE_SKILLS:
        _skill(tmp_path, name)

    all_skills = list_skills(tmp_path / "skills")
    pipeline = list_skills(tmp_path / "skills", pipeline_only=True)

    assert all_skills["skill_count"] == 5
    assert [item["directory_name"] for item in pipeline["skills"]] == sorted(
        cli.SCHEMATIC_PIPELINE_SKILLS
    )


def test_lists_saved_results_with_filters(tmp_path):
    report = tmp_path / "user" / "task" / "run" / "evaluation-report.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                "task_id": "task-1",
                "created_at": "2026-09-03T00:00:00Z",
                "status": "completed",
                "agent": "codex",
                "provider_model": "glm-4.7",
                "skills": ["example-marker"],
                "scores": {"overall_score": 88},
                "scoring": {"valid_for_ranking": True},
            }
        ),
        encoding="utf-8",
    )

    result = list_results(tmp_path, agent="codex", skill="example-marker")

    assert result["matched_count"] == 1
    assert result["results"][0]["overall_score"] == 88


def test_composes_all_four_pipeline_skills(tmp_path):
    for name in cli.SCHEMATIC_PIPELINE_SKILLS:
        _skill(tmp_path, name)

    bundle = compose_skill_bundle(tmp_path)

    content = (bundle / "SKILL.md").read_text(encoding="utf-8")
    assert all(name in content for name in cli.SCHEMATIC_PIPELINE_SKILLS)
    assert len(list((bundle / "skills").iterdir())) == 4


def test_prompt_batch_sends_same_prompt_to_each_agent(monkeypatch):
    seen = []

    def fake_check(args):
        seen.append((args.agent, args.prompt, args.model))
        return {"status": "connected", "agent": args.agent}

    monkeypatch.setattr(cli, "_check_agent", fake_check)
    result = cli._prompt_batch(
        argparse.Namespace(
            agent=["codex", "opencode"], profile="p", model="m",
            prompt="same", workers=2, timeout=10, database_verify=True,
        )
    )

    assert result["status"] == "completed"
    assert sorted(seen) == [("codex", "same", "m"), ("opencode", "same", "m")]


def test_multi_commands_parse_repeated_agents():
    args = cli._parser().parse_args(
        [
            "run-multi", "--skill", "example-marker",
            "--agent", "codex", "--agent", "opencode",
            "--model", "glm-4.7",
            "--prompt", "HI",
        ]
    )

    assert args.agent == ["codex", "opencode"]
    assert args.profile is None
    assert args.workers == 2


def test_evaluation_batch_runs_each_agent_with_same_prompt(tmp_path, monkeypatch):
    skill = _skill(tmp_path, "example-marker")
    seen = []

    def fake_run_evaluation(**kwargs):
        seen.append((kwargs["agent"], kwargs["prompt"], kwargs["selected_skills"]))
        return {
            "status": "completed",
            "task_id": kwargs["task_id"],
            "provider_model": kwargs["model"],
            "result_dir": str(tmp_path / kwargs["agent"]),
            "scores": {"overall_score": 90},
        }

    monkeypatch.setattr(cli, "run_evaluation", fake_run_evaluation)
    args = argparse.Namespace(
        agent=["codex", "opencode"], workers=2, model="glm-4.7",
        profile="litellm_glm_4_7", case=[], prompt="same task",
        must_contain=[], must_not_contain=[], parallelism=1, iterations=1,
        timeout=30, max_turns=2, benchmark=True, output_dir=None,
        database_trace=True, require_model_verification=True, user_id="local",
        task_name=None, llm_judge=True,
    )

    result = cli._evaluation_batch(
        args,
        skill_dir=skill,
        selected_skills=["example-marker"],
        evaluation_type="skill",
    )

    assert result["status"] == "completed"
    assert sorted(seen) == [
        ("codex", "same task", ["example-marker"]),
        ("opencode", "same task", ["example-marker"]),
    ]
