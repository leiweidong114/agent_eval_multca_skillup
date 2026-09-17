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


def test_schematic_profile_skill_count_has_no_artificial_upper_limit(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    many_skills = [f"skill-{index}" for index in range(1, 33)]
    profiles = {
        task_type: {"skills": list(many_skills), "evaluator_id": "schematic-default"}
        for task_type in SCHEMATIC_TASK_TYPES
    }

    saved = save_runtime_settings(backend, {
        "judge_model": "judge",
        "agent_test_model": "agent",
        "schematic_task_profiles": profiles,
    })

    assert saved["schematic_task_profiles"]["block_to_schematic"]["skills"] == many_skills
    assert load_runtime_settings(backend)["schematic_task_profiles"]["block_to_schematic"]["skills"] == many_skills
