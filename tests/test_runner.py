from pathlib import Path

from agent_eval.runner import aggregate_scores, build_eval_config


def test_eval_config_uses_local_multica_without_auth_or_database(tmp_path):
    config = build_eval_config(
        agent="codex",
        model="gpt-test",
        executable="codex",
        runtime_binary=tmp_path / "multica-eval-runtime.exe",
        skill_name="demo",
        case_paths=[Path("evals/cases/case.yaml")],
        parallelism=2,
        timeout_seconds=30,
        max_turns=3,
        benchmark=True,
        extra_args=[],
    )
    encoded = str(config).lower()
    assert config["engine"]["name"] == "multica-local"
    assert config["skills"][0]["target"] == ".agents/skills/demo"
    assert "login" not in encoded
    assert "token" not in encoded
    assert "database" not in encoded
    assert "litellm" not in encoded
    assert "system_prompt" not in encoded


def test_eval_config_matches_justdo_openclaw_bridge_contract(tmp_path):
    config = build_eval_config(
        agent="openclaw",
        model="main",
        executable="JustDo-agent",
        runtime_binary=tmp_path / "multica-eval-runtime",
        skill_name="demo",
        case_paths=[Path("evals/cases/case.yaml")],
        parallelism=1,
        timeout_seconds=1800,
        max_turns=12,
        benchmark=True,
        extra_args=[],
    )

    assert config["skills"][0]["target"] == "skills/demo"
    args = config["engine"]["custom"]["local"]["args"]
    assert args == [
        "--input",
        "${input_file}",
        "--output",
        "${output_file}",
        "--agent",
        "openclaw",
        "--model",
        "${model_name}",
        "--timeout-seconds",
        "1800",
        "--max-turns",
        "12",
    ]
    assert config["engine"]["model"]["name"] == "main"


def test_aggregate_scores_reports_task_baseline_gain_and_stability():
    scores = aggregate_scores(
        [
            {
                "overall_tokens": 123,
                "case_results": [
                    {
                        "configuration": "with_skill",
                        "status": "PASS",
                        "duration_ms": 10,
                        "grading": {"summary": {"passed": 2, "total": 2}},
                    },
                    {
                        "configuration": "without_skill",
                        "status": "FAIL",
                        "duration_ms": 20,
                        "grading": {"summary": {"passed": 0, "total": 2}},
                    },
                ],
            }
        ]
    )
    assert scores["task_score"] == 100
    assert scores["baseline_score"] == 0
    assert scores["skill_gain"] == 100
    assert scores["execution_stability"] == 100
    assert scores["total_tokens"] == 123
