from pathlib import Path
import os
import sys
from threading import Event

import pytest

from agent_eval.runner import (
    EvaluationCancelled,
    _execute_process,
    aggregate_scores,
    attach_session_evidence,
    build_eval_config,
    classify_evaluation_failure,
)
from agent_eval.runtime import SUPPORTED_AGENTS, agent_capabilities, backend_agent


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


def test_specified_model_and_skill_config_matrix_covers_every_capable_agent(tmp_path):
    capable = []
    for agent in SUPPORTED_AGENTS:
        if not agent_capabilities(agent)["specified_model_and_skill_evaluation"]:
            continue
        capable.append(agent)
        config = build_eval_config(
            agent=backend_agent(agent),
            model="matrix-model",
            executable="ignored-by-config",
            runtime_binary=tmp_path / "multica-eval-runtime.exe",
            skill_name="matrix-skill",
            case_paths=[Path("evals/cases/matrix.yaml")],
            parallelism=1,
            timeout_seconds=30,
            max_turns=2,
            benchmark=False,
            extra_args=[],
        )
        assert config["engine"]["model"]["name"] == "matrix-model"
        assert config["skills"][0]["target"].endswith("/matrix-skill")

    assert set(capable) == {
        agent
        for agent in SUPPORTED_AGENTS
        if agent not in {"dim", "hermes", "mcode", "qwenpaw", "zeroclaw"}
    }


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


def test_aggregate_scores_handles_error_cases_without_grading():
    scores = aggregate_scores(
        [
            {
                "case_results": [
                    {
                        "configuration": "with_skill",
                        "status": "ERROR",
                        "duration_ms": 25,
                        "grading": None,
                    }
                ]
            }
        ]
    )

    assert scores["task_score"] is None
    assert scores["execution_stability"] == 0
    assert scores["with_skill_cases"] == 1


def test_session_evidence_is_attached_to_matching_case(tmp_path):
    session_dir = tmp_path / "marker" / "with_skill" / "outputs" / "agent" / "run"
    session_dir.mkdir(parents=True)
    (session_dir / "session-result.json").write_text(
        '{"final_message":"OK","transcript":[{"role":"tool_call"}]}',
        encoding="utf-8",
    )
    result = {
        "case_results": [
            {"case_id": "marker", "configuration": "with_skill"}
        ]
    }

    enriched = attach_session_evidence(tmp_path, result)

    assert enriched["case_results"][0]["session_result"]["final_message"] == "OK"


def test_background_process_can_be_cancelled(tmp_path):
    cancelled = Event()
    cancelled.set()
    with pytest.raises(EvaluationCancelled):
        _execute_process(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=tmp_path,
            env=dict(os.environ),
            cancel_event=cancelled,
        )


@pytest.mark.parametrize(
    ("message", "category", "retryable"),
    [
        ("HTTP 429 Too Many Requests", "gateway_rate_limited", True),
        ("Token Plan usage has reached the usage limit", "gateway_quota_exhausted", False),
        ("upstream returned 503", "gateway_server_error", True),
        ("connection reset by peer", "gateway_unavailable", True),
        ("unrecognized_model", "model_incompatible", False),
        ("legacy workspace; run openclaw doctor --fix", "agent_workspace_invalid", False),
    ],
)
def test_external_failures_are_classified(message, category, retryable):
    result = classify_evaluation_failure(message, 1)
    assert result["category"] == category
    assert result["retryable"] is retryable
