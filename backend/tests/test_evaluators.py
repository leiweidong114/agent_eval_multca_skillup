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


def install_bundled_schematic_plugin(backend: Path) -> None:
    target = backend / "evaluator_plugins" / "schematic-default"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SCHEMATIC_PLUGIN, target)


def test_builtin_evaluator_defaults_preserve_skill_and_schematic_modes(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    install_bundled_schematic_plugin(backend)

    assert resolve_evaluator(backend, evaluation_type="skill").id == "generic"
    schematic = resolve_evaluator(backend, evaluation_type="schematic")
    assert schematic.id == "schematic-default"
    catalog = list_evaluators(backend)
    assert {item["id"] for item in catalog} == {
        "generic",
        "schematic-default",
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
        results=[{"case_results": [{"status": "PASS", "response": url}]}],
        interactions=[],
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
