from pathlib import Path

from agent_eval.offline_doctor import run_doctor


def test_offline_doctor_reports_missing_runtime_without_crashing(tmp_path: Path):
    backend = tmp_path / "backend"
    (backend / "config").mkdir(parents=True)
    (backend / "config" / "models.yaml").write_text(
        "default_profile: native\nprofiles:\n  native:\n    type: native\n    model: demo\n",
        encoding="utf-8",
    )
    (backend / "config" / "database.yaml").write_text(
        "database:\n  enabled: false\n", encoding="utf-8"
    )
    result = run_doctor(backend)
    assert result["status"] == "error"
    assert {item["name"] for item in result["checks"]} >= {
        "skill_up", "multica_runtime", "frontend_dist", "database", "agent"
    }
