from agent_eval.env_config import load_root_env
from agent_eval.model_config import load_runtime_settings, save_runtime_settings
from agent_eval.schematic_tasks import SCHEMATIC_TASK_TYPES


def test_legacy_schematic_settings_migrate_to_block_to_schematic(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (tmp_path / ".env").write_text(
        'SCHEMATIC_SKILLS_JSON=["legacy-a","legacy-b"]\n'
        "DEFAULT_SCHEMATIC_EVALUATOR=legacy-evaluator\n",
        encoding="utf-8",
    )

    settings = load_runtime_settings(backend)

    profile = settings["schematic_task_profiles"]["block_to_schematic"]
    assert profile["skills"] == ["legacy-a", "legacy-b"]
    assert profile["evaluator_id"] == "legacy-evaluator"
    assert set(settings["schematic_task_profiles"]) == set(SCHEMATIC_TASK_TYPES)


def test_web_settings_persist_all_three_schematic_task_profiles(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    profiles = {
        "block_to_schematic": {"skills": ["a"], "evaluator_id": "eval-a"},
        "block_to_signal_list": {"skills": ["b"], "evaluator_id": "eval-b"},
        "signal_list_to_schematic": {"skills": ["c"], "evaluator_id": "eval-c"},
    }

    saved = save_runtime_settings(backend, {
        "judge_model": "judge",
        "agent_test_model": "agent",
        "schematic_task_profiles": profiles,
    })

    assert saved["schematic_task_profiles"] == profiles
    environment = load_root_env(backend)
    assert environment["SCHEMATIC_TASK_PROFILES_JSON"]
    assert load_runtime_settings(backend)["schematic_task_profiles"] == profiles
