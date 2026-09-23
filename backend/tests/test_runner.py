from pathlib import Path
import json
import os
import sys
from threading import Event

import pytest

from agent_eval.runner import (
    EvaluationCancelled,
    _configure_skillup_workspace,
    _copy_skill,
    _execute_process,
    _prepare_staged_skill,
    aggregate_scores,
    attach_session_evidence,
    build_eval_config,
    classify_evaluation_failure,
    cases_completed,
)
from agent_eval.results_paths import evaluation_results_root, evaluation_results_roots, validate_results_root
from agent_eval.windows_paths import filesystem_path
from agent_eval.runtime import SUPPORTED_AGENTS, agent_capabilities, backend_agent


def test_results_root_from_environment_is_shared_with_default(tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    backend.mkdir()
    monkeypatch.delenv("AGENT_EVAL_RESULTS_ROOT", raising=False)
    assert evaluation_results_root(backend) == backend / "evaluation_results"
    desired = tmp_path / "short-results"
    (tmp_path / ".env").write_text(
        f"AGENT_EVAL_RESULTS_ROOT={desired.as_posix()}\n", encoding="utf-8"
    )
    assert evaluation_results_root(backend) == desired.resolve()


def test_results_root_switch_keeps_prior_locations_readable(tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    backend.mkdir()
    monkeypatch.delenv("AGENT_EVAL_RESULTS_ROOT", raising=False)
    first = tmp_path / "first"
    second = tmp_path / "second"
    (tmp_path / ".env").write_text(f"AGENT_EVAL_RESULTS_ROOT={first.as_posix()}\n", encoding="utf-8")
    assert evaluation_results_root(backend) == first.resolve()
    (tmp_path / ".env").write_text(
        f"AGENT_EVAL_RESULTS_ROOT={second.as_posix()}\n"
        f'AGENT_EVAL_RESULTS_ROOT_HISTORY_JSON=["{first.as_posix()}"]\n',
        encoding="utf-8",
    )
    assert evaluation_results_root(backend) == second.resolve()
    assert evaluation_results_roots(backend) == (
        second.resolve(), (backend / "evaluation_results").resolve(), first.resolve(),
    )
    with pytest.raises(ValueError, match="绝对路径"):
        validate_results_root("relative/run-results")


def test_copy_skill_supports_deep_destination(tmp_path):
    source = tmp_path / "source"
    nested = source / "skills" / "02-diagram-logical-connection-mapping" / "rules"
    nested.mkdir(parents=True)
    (source / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    name = "natural_language_mapping_rules_template.md"
    (nested / name).write_text("rule content", encoding="utf-8")
    target = tmp_path / ("deep-directory-" * 4) / ("run-directory-" * 4) / "staging" / "skill"
    copied = target / "skills" / "02-diagram-logical-connection-mapping" / "rules" / name
    if os.name == "nt":
        assert len(str(copied)) >= 260
    _copy_skill(source, target)
    assert filesystem_path(copied).read_text(encoding="utf-8") == "rule content"


def test_staged_bundle_uses_actual_agent_skill_root_and_writes_manifest(tmp_path):
    source = tmp_path / "schematic-pipeline-bundle-test"
    child = source / "skills" / "01-schematic-pipeline"
    child.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "resolve .agents/skills/schematic-pipeline-bundle-test\n", encoding="utf-8"
    )
    (child / "SKILL.md").write_text(
        "resolve .agents/skills/<bundle-name>\n", encoding="utf-8"
    )
    staged = tmp_path / "run" / "staging" / "skill"
    _copy_skill(source, staged)

    manifest = _prepare_staged_skill(
        staged, source_skill=source, agent="openclaw",
        selected_skills=["schematic-pipeline"],
    )

    assert manifest["installed_target"] == "skills/schematic-pipeline-bundle-test"
    assert manifest["verified"] is True
    assert "skills/schematic-pipeline-bundle-test" in (staged / "SKILL.md").read_text(encoding="utf-8")
    assert "skills/schematic-pipeline-bundle-test" in (child_md := staged / "skills" / "01-schematic-pipeline" / "SKILL.md").read_text(encoding="utf-8")
    assert "<bundle-name>" not in child_md.read_text(encoding="utf-8")
    assert child_md.is_file()
    assert (staged.parent / "skill-staging-manifest.json").is_file()


def test_skillup_temp_workspace_is_scoped_to_run_directory(tmp_path):
    env = {"TEMP": "old", "TMP": "old"}
    result_root = tmp_path / "runs" / "one"
    configured = _configure_skillup_workspace(env, result_root)

    assert configured == result_root / "runtime" / "skill-up-temp"
    assert configured.is_dir()
    assert env["TEMP"] == env["TMP"] == env["TMPDIR"] == str(configured)


def test_negative_control_failure_is_scored_evidence_not_runtime_failure():
    assert cases_completed([{"case_results": [{"status": "PASS"}, {"status": "FAIL"}]}])
    assert not cases_completed([{"case_results": [{"status": "PASS"}, {"status": "ERROR"}]}])
    assert not cases_completed([])


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
    artifacts = config["cases"]["defaults"]["collect_artifacts"]
    assert "out/**" in artifacts
    assert "figures/**" in artifacts


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


def test_eval_config_installs_composed_child_skills_as_first_class_skills(tmp_path):
    config = build_eval_config(
        agent="openclaw",
        model="main",
        executable="openclaw",
        runtime_binary=tmp_path / "multica-eval-runtime",
        skill_name="combined-demo",
        case_paths=[Path("evals/cases/case.yaml")],
        parallelism=1,
        timeout_seconds=30,
        max_turns=12,
        benchmark=False,
        extra_args=[],
        additional_skills=[
            ("skills/01-schematic-pipeline", "schematic-pipeline"),
            ("skills/02-signal-interface-generation", "signal-interface-generation"),
        ],
    )

    assert config["skills"] == [
        {
            "source": "local_path",
            "path": ".",
            "target": "skills/combined-demo",
            "exclude": ["evals/**"],
        },
        {
            "source": "local_path",
            "path": "skills/01-schematic-pipeline",
            "target": "skills/schematic-pipeline",
            "exclude": ["evals/**"],
        },
        {
            "source": "local_path",
            "path": "skills/02-signal-interface-generation",
            "target": "skills/signal-interface-generation",
            "exclude": ["evals/**"],
        },
    ]


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


def test_session_evidence_deduplicates_agent_and_workspace_copies(tmp_path):
    payload = {
        "session_id": "same-session",
        "final_message": "完成",
        "transcript": [
            {"role": "user", "content": "生成原理图", "turn": 1},
            {"role": "assistant", "content": "完成", "turn": 1},
            {
                "role": "assistant",
                "content": "AGENT_EVAL_TELEMETRY_JSON:{\"total_tokens\":1}",
                "turn": 1,
            },
            {"role": "assistant", "content": "完成", "turn": 1},
        ],
    }
    for relative in (
        "marker/with_skill/outputs/agent/run",
        "marker/with_skill/outputs/workspace/outputs",
    ):
        session_dir = tmp_path / relative
        session_dir.mkdir(parents=True)
        (session_dir / "session-result.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    result = {
        "case_results": [
            {"case_id": "marker", "configuration": "with_skill"}
        ]
    }

    enriched = attach_session_evidence(tmp_path, result)

    case = enriched["case_results"][0]
    assert "session_results" not in case
    assert [item["role"] for item in case["session_result"]["transcript"]] == [
        "user",
        "assistant",
    ]


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


def test_process_output_is_forwarded_as_live_events(tmp_path):
    events = []
    result = _execute_process(
        [sys.executable, "-c", "import sys; print('model says hi'); print('tool warning', file=sys.stderr)"],
        cwd=tmp_path,
        env=dict(os.environ),
        cancel_event=None,
        event_callback=lambda kind, content: events.append((kind, content)),
    )

    assert result.returncode == 0
    assert ("stdout", "model says hi") in events
    assert ("stderr", "tool warning") in events


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
