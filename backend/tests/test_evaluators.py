from pathlib import Path
import shutil

import pytest

from agent_eval.evaluators import list_evaluators, resolve_evaluator
from agent_eval.evaluators.protocol import (
    EVALUATOR_API_VERSION,
    EvaluationContext,
    EvaluationEvidence,
)
from agent_eval.evaluators.artifacts import build_artifact_manifest


BUNDLED_SCHEMATIC_PLUGIN = (
    Path(__file__).resolve().parents[1] / "evaluator_plugins" / "schematic-default"
)
BUNDLED_SKILL_PLUGIN = (
    Path(__file__).resolve().parents[1] / "evaluator_plugins" / "skill-default"
)


def install_bundled_schematic_plugin(backend: Path) -> None:
    target = backend / "evaluator_plugins" / "schematic-default"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SCHEMATIC_PLUGIN, target)


def install_bundled_skill_plugin(backend: Path) -> None:
    target = backend / "evaluator_plugins" / "skill-default"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL_PLUGIN, target)


def test_builtin_evaluator_defaults_preserve_skill_and_schematic_modes(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    install_bundled_schematic_plugin(backend)
    install_bundled_skill_plugin(backend)

    assert resolve_evaluator(backend, evaluation_type="skill").id == "skill-default"
    schematic = resolve_evaluator(backend, evaluation_type="schematic")
    assert schematic.id == "schematic-default"
    catalog = list_evaluators(backend)
    assert {item["id"] for item in catalog} == {
        "generic",
        "schematic-default",
        "skill-default",
    }
    assert next(item for item in catalog if item["id"] == "schematic-default")["source"] == "bundled"


def test_external_evaluator_is_loaded_only_from_configured_roots(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    plugin_dir = tmp_path / "private-evaluators" / "large"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "private_rules.py").write_text(
        "def extensions():\n    return {'private': True}\n",
        encoding="utf-8",
    )
    (plugin_dir / "evaluator.py").write_text(
        "from agent_eval.evaluators.protocol import EVALUATOR_API_VERSION, PluginEvaluation\n"
        "from .private_rules import extensions\n"
        "class PrivateEvaluator:\n"
        "    id = 'schematic-large'\n"
        "    version = '7'\n"
        "    api_version = EVALUATOR_API_VERSION\n"
        "    evaluation_types = ('schematic',)\n"
        "    def evaluate(self, *, context, evidence, scoring_config):\n"
        "        return PluginEvaluation({}, {}, extensions=extensions())\n"
        "PLUGIN = PrivateEvaluator()\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f'EVALUATOR_PLUGIN_PATHS_JSON=["{plugin_dir.parent.as_posix()}"]\n'
        "DEFAULT_SCHEMATIC_EVALUATOR=schematic-large\n",
        encoding="utf-8",
    )

    plugin = resolve_evaluator(backend, evaluation_type="schematic")
    result = plugin.evaluate(
        context=EvaluationContext(
            run_id="run", task_id="task", evaluation_type="schematic",
            agent="justdo", requested_model="model", skill_name="skill",
            selected_skills=("skill",), skill_md="# Skill",
        ),
        evidence=EvaluationEvidence({}, {}, {}, {}, [], []),
        scoring_config={},
    )

    assert plugin.id == "schematic-large"
    assert plugin.api_version == EVALUATOR_API_VERSION
    assert result.extensions == {"private": True}
    with pytest.raises(ValueError, match="does not support skill"):
        resolve_evaluator(backend, evaluation_type="skill", evaluator_id="schematic-large")


def test_unknown_evaluator_is_rejected_before_execution(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    with pytest.raises(ValueError, match="not installed"):
        resolve_evaluator(backend, evaluation_type="skill", evaluator_id="private-missing")


def test_project_extension_evaluator_is_discovered_without_env_path(tmp_path):
    backend = tmp_path / "backend"
    plugin_dir = backend / "extensions" / "evaluators" / "block-list"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "evaluator.py").write_text(
        "from agent_eval.evaluators.protocol import EVALUATOR_API_VERSION, PluginEvaluation\n"
        "class Evaluator:\n"
        "    id='block-list'\n"
        "    version='1'\n"
        "    api_version=EVALUATOR_API_VERSION\n"
        "    evaluation_types=('schematic',)\n"
        "    schematic_task_types=('block_to_signal_list',)\n"
        "    def evaluate(self, *, context, evidence, scoring_config):\n"
        "        return PluginEvaluation({}, {})\n"
        "PLUGIN=Evaluator()\n",
        encoding="utf-8",
    )

    assert resolve_evaluator(
        backend,
        evaluation_type="schematic",
        evaluator_id="block-list",
        schematic_task_type="block_to_signal_list",
    ).id == "block-list"
    with pytest.raises(ValueError, match="does not support schematic task"):
        resolve_evaluator(
            backend,
            evaluation_type="schematic",
            evaluator_id="block-list",
            schematic_task_type="block_to_schematic",
        )


def test_schematic_evaluator_requires_real_pipeline_delivery(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    install_bundled_schematic_plugin(backend)
    output = (
        tmp_path / "run" / "skill-up" / "iteration-1" / "case"
        / "with_skill" / "outputs" / "workspace" / "out"
    )
    (output / "layout").mkdir(parents=True)
    (output / "sheets_markdown").mkdir()
    (output / "frags" / "S1").mkdir(parents=True)
    (output / "catalog.json").write_text('{"components":[]}', encoding="utf-8")
    (output / "sheets_markdown" / "S1.md").write_text("# S1", encoding="utf-8")
    (output / "frags" / "S1" / "base.txt").write_text("base", encoding="utf-8")
    (output / "frags" / "S1" / "slices.json").write_text("[]", encoding="utf-8")
    workspace_clutter = output.parent / ".opencode" / "node_modules"
    workspace_clutter.mkdir(parents=True)
    (workspace_clutter / "package.json").write_text("{}", encoding="utf-8")
    (output / "sheets.json").write_text(
        '{"sheets":[{"sheet_id":"S1","components":[],"nets":[]}]}', encoding="utf-8"
    )
    (output / "layout" / "S1.json").write_text(
        '{"metrics":{"component_overlap_count":0,"unrouted_net_count":0}}', encoding="utf-8"
    )
    url = "http://127.0.0.1:8631/static_schematic/project/shell.html"
    (output / "apply_result.json").write_text(
        '{"project_id":"project","sheet_count":1,"url":"' + url
        + '","url_verification":{"status":"ok","http_status":200}}',
        encoding="utf-8",
    )
    evidence = EvaluationEvidence(
        deterministic_scores={},
        process_metrics={"subagent_calls": 2},
        skill_usage={"all_selected_skills_read": True, "all_selected_skills_observed": True},
        skill_quality={"score": 100, "details": []},
        results=[{"case_results": [{"configuration": "with_skill", "status": "PASS", "response": url, "session_results": [{
            "transcript": [
                *[
                    {"role": "tool_call", "tool_call": {"id": f"call-{index}", "name": "exec_command", "arguments": {"cmd": f"python skills/scripts/{script}"}}}
                    for index, script in enumerate(("validate_sheets.py", "render_sheets_markdown.py", "codegen_base.py", "layout_sheet.py", "apply.py"), 1)
                ],
                *[
                    {"role": "tool_result", "tool_result": {"call_id": f"call-{index}", "status": "completed", "content": "ok"}}
                    for index in range(1, 6)
                ],
            ]
        }]}, {"configuration": "without_skill", "status": "FAIL", "response": "Expected negative control"}]}],
        interactions=[
            {
                "response": {"choices": [{"message": {"tool_calls": [{
                    "id": "plan-call",
                    "function": {"name": "update_plan", "arguments": '{"step":"python fetch_catalog.py"}'},
                }]}}]},
                "proxy_server_request": {"messages": []},
            },
            {
                "response": {"choices": [{"message": {"tool_calls": [{
                    "id": "db-fetch", "function": {
                        "name": "shell_command", "arguments": '{"command":"python fetch_catalog.py"}',
                    },
                }]}}]},
                "proxy_server_request": {"messages": []},
            },
            {
                "response": {"choices": [{"message": {}}]},
                "proxy_server_request": {"messages": [{
                    "role": "tool", "tool_call_id": "db-fetch",
                    "content": "Exit code: 0\nOutput: catalog written",
                }]},
            },
        ],
        artifact_root=str(tmp_path / "run"),
        artifact_manifest=build_artifact_manifest(tmp_path / "run"),
    )
    context = EvaluationContext(
        run_id="run", task_id="task", evaluation_type="schematic",
        agent="codex", requested_model="model", skill_name="bundle",
        selected_skills=("schematic-pipeline",), skill_md="# Skill",
        schematic_task_type="block_to_schematic",
    )
    result = resolve_evaluator(backend, evaluation_type="schematic").evaluate(
        context=context, evidence=evidence, scoring_config={}
    )
    acceptance = result.extensions["schematic"]["acceptance"]

    assert acceptance["accepted"] is True
    assert acceptance["score"] == 100
    assert result.rule_dimensions["result"]["score"] == 100
    trace = result.extensions["schematic"]["trace"]
    assert trace["schema_version"] == "schematic-trace-v2"
    assert trace["script_success_rate"] == 100
    assert trace["script_calls"] == 6  # update_plan prose is not a script invocation
    assert trace["assertion_completion_rate"] == 100
    assert trace["artifact_count"] == 7
    assert trace["workspace_file_count"] == 8
    assert acceptance["artifact_count"] == 7

    evidence.results[0]["case_results"][0]["session_results"][0]["transcript"][6]["tool_result"]["content"] = (
        "Exit code: 1\nOutput: validation failed"
    )
    failed = resolve_evaluator(backend, evaluation_type="schematic").evaluate(
        context=context, evidence=evidence, scoring_config={}
    )
    failed_trace = failed.extensions["schematic"]["trace"]
    assert failed_trace["tool_completion_rate"] == pytest.approx(83.33, abs=0.01)
    assert failed_trace["structured_tool_result_failures"] == 1
    assert failed.extensions["schematic"]["acceptance"]["accepted"] is False


def test_schematic_evaluator_rejects_process_only_success(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    install_bundled_schematic_plugin(backend)
    evidence = EvaluationEvidence(
        deterministic_scores={},
        process_metrics={"subagent_calls": 0},
        skill_usage={"all_selected_skills_read": True, "all_selected_skills_observed": True},
        skill_quality={"score": 100, "details": []},
        results=[{"case_results": [{"status": "PASS", "response": "still working"}]}],
        interactions=[], artifact_root=str(tmp_path),
        artifact_manifest=build_artifact_manifest(tmp_path),
    )
    context = EvaluationContext(
        run_id="run", task_id="task", evaluation_type="schematic",
        agent="codex", requested_model="model", skill_name="bundle",
        selected_skills=("schematic-pipeline",), skill_md="# Skill",
        schematic_task_type="block_to_schematic",
    )
    result = resolve_evaluator(backend, evaluation_type="schematic").evaluate(
        context=context, evidence=evidence, scoring_config={}
    )

    assert result.extensions["schematic"]["acceptance"]["accepted"] is False
    assert "schematic_url_verified" in result.extensions["schematic"]["acceptance"]["failed_checks"]


@pytest.mark.parametrize("tool_name", ["exec", "PowerShell"])
def test_schematic_trace_correlates_justdo_tool_ids(tmp_path, tool_name):
    backend = tmp_path / "backend"
    backend.mkdir()
    install_bundled_schematic_plugin(backend)
    call_id = "call_267c52e34b224d7d9e70c688"
    evidence = EvaluationEvidence(
        deterministic_scores={}, process_metrics={}, skill_usage={}, skill_quality={},
        results=[], artifact_root=str(tmp_path), artifact_manifest=[],
        interactions=[
            {
                "response": {"choices": [{"message": {"tool_calls": [{
                    "id": call_id,
                    "function": {"name": tool_name, "arguments": '{"command":"python fetch_catalog.py"}'},
                }]}}]},
                "proxy_server_request": {"messages": []},
            },
            {
                "response": {"choices": []},
                "proxy_server_request": {"messages": [{
                    "role": "tool", "tool_call_id": call_id.replace("call_", "call"),
                    "content": "catalog written: out/catalog.json",
                }]},
            },
        ],
    )
    context = EvaluationContext(
        run_id="run", task_id="task", evaluation_type="schematic",
        agent="justdo", requested_model="model", skill_name="bundle",
        selected_skills=("schematic-pipeline",), skill_md="# Skill",
        schematic_task_type="block_to_schematic",
    )
    result = resolve_evaluator(backend, evaluation_type="schematic").evaluate(
        context=context, evidence=evidence, scoring_config={},
    )
    trace = result.extensions["schematic"]["trace"]
    fetch = next(item for item in trace["script_evidence"] if item["script"] == "fetch_catalog.py")
    assert fetch["attempts"] == 1
    assert fetch["successful_calls"] == 1
    assert trace["structured_tool_result_failures"] == 0


def test_schematic_trace_distinguishes_failed_script_retry(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    install_bundled_schematic_plugin(backend)
    ids = ("call_1111111111111111", "call_2222222222222222")
    evidence = EvaluationEvidence(
        deterministic_scores={}, process_metrics={}, skill_usage={}, skill_quality={},
        results=[], artifact_root=str(tmp_path), artifact_manifest=[],
        interactions=[
            {
                "response": {"choices": [{"message": {"tool_calls": [{
                    "id": call_id,
                    "function": {"name": "exec", "arguments": '{"command":"python layout_sheet.py"}'},
                }]}}]},
                "proxy_server_request": {"messages": [{
                    "role": "tool", "tool_call_id": call_id.replace("call_", "call"),
                    "content": content,
                }]},
            }
            for call_id, content in zip(ids, ("切片完整性校验失败", '{"status": "ok"}'))
        ],
    )
    context = EvaluationContext(
        run_id="run", task_id="task", evaluation_type="schematic",
        agent="justdo", requested_model="model", skill_name="bundle",
        selected_skills=("schematic-pipeline",), skill_md="# Skill",
        schematic_task_type="block_to_schematic",
    )
    result = resolve_evaluator(backend, evaluation_type="schematic").evaluate(
        context=context, evidence=evidence, scoring_config={},
    )
    layout = next(item for item in result.extensions["schematic"]["trace"]["script_evidence"]
                  if item["script"] == "layout_sheet.py")
    assert layout["attempts"] == 2
    assert layout["successful_calls"] == 1


def test_skill_evaluator_requires_real_marker_artifact(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    install_bundled_skill_plugin(backend)
    output = (
        tmp_path / "run" / "skill-up" / "iteration-1" / "case"
        / "with_skill" / "outputs" / "workspace" / "artifacts"
    )
    output.mkdir(parents=True)
    (output / "verification.txt").write_text("MULTICA_SKILL_UP_OK", encoding="utf-8")
    evidence = EvaluationEvidence(
        deterministic_scores={},
        process_metrics={"tool_calls": 1},
        skill_usage={"all_selected_skills_read": True, "all_selected_skills_observed": True},
        skill_quality={"score": 100, "details": []},
        results=[{"case_results": [{"configuration": "with_skill", "status": "PASS", "response": "MULTICA_SKILL_UP_OK"}]}],
        interactions=[],
        artifact_root=str(tmp_path / "run"),
        artifact_manifest=build_artifact_manifest(tmp_path / "run"),
    )
    context = EvaluationContext(
        run_id="run", task_id="task", evaluation_type="skill",
        agent="claude", requested_model="model", skill_name="example-marker",
        selected_skills=("example-marker",), skill_md="# Skill",
    )

    result = resolve_evaluator(backend, evaluation_type="skill").evaluate(
        context=context, evidence=evidence, scoring_config={}
    )

    assert result.extensions["skill"]["acceptance"]["accepted"] is True
    assert result.rule_dimensions["result"]["score"] == 100


def test_skill_evaluator_rejects_text_only_marker_claim(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    install_bundled_skill_plugin(backend)
    evidence = EvaluationEvidence(
        deterministic_scores={}, process_metrics={"tool_calls": 1},
        skill_usage={"all_selected_skills_read": True, "all_selected_skills_observed": True},
        skill_quality={},
        results=[{"case_results": [{"configuration": "with_skill", "status": "PASS", "response": "MULTICA_SKILL_UP_OK"}]}],
        interactions=[], artifact_root=str(tmp_path),
        artifact_manifest=build_artifact_manifest(tmp_path),
    )
    context = EvaluationContext(
        run_id="run", task_id="task", evaluation_type="skill",
        agent="codebuddy", requested_model="model", skill_name="example-marker",
        selected_skills=("example-marker",), skill_md="# Skill",
    )

    result = resolve_evaluator(backend, evaluation_type="skill").evaluate(
        context=context, evidence=evidence, scoring_config={}
    )

    assert result.extensions["skill"]["acceptance"]["accepted"] is False
    assert "required_artifacts_exist" in result.extensions["skill"]["acceptance"]["failed_checks"]
