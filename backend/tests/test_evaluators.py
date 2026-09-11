from pathlib import Path
import shutil

import pytest

from agent_eval.evaluators import list_evaluators, resolve_evaluator
from agent_eval.evaluators.protocol import (
    EVALUATOR_API_VERSION,
    EvaluationContext,
    EvaluationEvidence,
)


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
