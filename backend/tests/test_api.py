import os

from fastapi.testclient import TestClient

from app.main import app
from app.api.routes_eval import RunRequest, _apply_schematic_skill_settings


client = TestClient(app)
assert client.post(
    "/api/auth/login", json={"employee_no": "test-worker", "password": "ignored"}
).status_code == 200


def test_login_is_required_and_password_is_not_returned():
    anonymous = TestClient(app)
    assert anonymous.get("/api/agents").status_code == 401
    response = anonymous.post(
        "/api/auth/login", json={"employee_no": "E10001", "password": "not-validated"}
    )
    assert response.status_code == 200
    assert response.json()["employee_no"] == "E10001"
    assert "password" not in response.text.lower()
    assert anonymous.get("/api/auth/me").json()["employee_no"] == "E10001"


def test_health_and_discovery_endpoints(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes_skill.load_runtime_settings",
        lambda root: {"judge_model": "test-judge-model"},
    )
    assert client.get("/api/health").json()["status"] == "ok"
    agents = client.get("/api/agents")
    assert agents.status_code == 200
    assert any(item["agent"] == "codex" for item in agents.json())
    by_name = {item["agent"]: item for item in agents.json()}
    assert by_name["codex"]["capabilities"]["specified_model_and_skill_evaluation"] is True
    assert by_name["mcode"]["capabilities"]["model_selection"] is False
    assert by_name["dim"]["capabilities"]["skill_injection"] is False
    assert any(item["agent"] == "justdo" for item in agents.json())
    skills = client.get("/api/skills")
    assert skills.status_code == 200
    model_config = client.get("/api/model-config")
    assert model_config.status_code == 200
    assert "profile" not in model_config.json()["llm_judge"]
    assert model_config.json()["llm_judge"]["model"] == "test-judge-model"
    evaluators = client.get("/api/evaluators")
    assert evaluators.status_code == 200
    assert {item["id"] for item in evaluators.json()} >= {"generic", "schematic-default"}
    task_types = client.get("/api/schematic-task-types")
    assert task_types.status_code == 200
    assert {item["id"] for item in task_types.json()} == {
        "block_to_schematic", "block_to_signal_list", "signal_list_to_schematic",
    }


def test_agent_path_endpoint_persists_shared_executable(tmp_path, monkeypatch):
    executable = tmp_path / ("JustDo-agent.cmd" if os.name == "nt" else "JustDo-agent")
    executable.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n", encoding="utf-8")
    if os.name != "nt":
        executable.chmod(0o755)
    monkeypatch.setattr("app.api.routes_skill.BACKEND_ROOT", tmp_path)

    response = client.put("/api/agents/justdo/path", json={"path": str(executable)})

    assert response.status_code == 200
    assert response.json()["configured_path"] == str(executable.resolve())
    listed = {item["agent"]: item for item in client.get("/api/agents").json()}
    assert listed["justdo"]["detected_executable"] == str(executable.resolve())
    assert "AGENT_PATHS_JSON=" in (tmp_path / ".env").read_text(encoding="utf-8")

    reset = client.put("/api/agents/justdo/path", json={"path": ""})
    assert reset.status_code == 200
    assert reset.json()["configured_path"] is None


def test_database_health_never_exposes_credentials_or_crashes():
    response = client.get("/api/database/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "error", "disabled"}
    assert "password" not in payload
    assert "database_url" not in payload


def test_runtime_settings_save_non_secret_default_models(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "models.yaml").write_text(
        "litellm:\n  model: fallback-model\n  api_base: https://gateway.example/v1\n  api_key_env: LITELLM_API_KEY\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.api.routes_skill.BACKEND_ROOT", tmp_path)
    response = client.put(
        "/api/settings",
        json={"judge_model": "judge-model", "agent_test_model": "agent-model"},
    )

    assert response.status_code == 200
    assert client.get("/api/settings").json()["judge_model"] == "judge-model"
    saved = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "judge-model" in saved
    assert "api_key" not in saved.lower()


def test_batch_model_probe_endpoint_returns_real_probe_summary(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes_skill.refresh_litellm_model_catalog",
        lambda root, **kwargs: {
            "connectivity_tested": True,
            "available_model_count": 2,
            "unavailable_model_count": 1,
            "models": [{"id": "a", "available": True}, {"id": "b", "available": True}],
            "unavailable_models": [{"id": "c", "available": False}],
        },
    )
    response = client.post(
        "/api/models/test-batch",
        json={"workers": 4, "timeout_seconds": 12},
    )

    assert response.status_code == 200
    assert response.json()["available_model_count"] == 2


def test_run_rejects_an_unsupported_model_or_skill_contract_before_queueing():
    base = {
        "skill": "example-marker",
        "profile": "native_codex",
        "prompt": "Return the marker",
    }
    model_response = client.post("/api/run", json={**base, "agent": "mcode"})
    assert model_response.status_code == 400
    assert "specified model" in model_response.json()["detail"]

    skill_response = client.post("/api/run", json={**base, "agent": "dim"})
    assert skill_response.status_code == 400
    assert "specified Skill" in skill_response.json()["detail"]


def test_run_request_supports_single_and_joint_skill_payloads():
    legacy = RunRequest(agent="codex", skill="example-marker", prompt="test")
    joint = RunRequest(
        agent="codex",
        skills=["example-marker", "schematic-generation"],
        prompt="test",
    )

    assert legacy.skills == ["example-marker"]
    assert joint.skill == "example-marker"
    assert joint.skills == ["example-marker", "schematic-generation"]


def test_run_request_accepts_a_stable_evaluator_id():
    request = RunRequest(
        agent="codex",
        skill="example-marker",
        prompt="test",
        evaluator_id="private-evaluator-v2",
    )
    assert request.evaluator_id == "private-evaluator-v2"


def test_schematic_run_request_defaults_to_block_to_schematic():
    request = RunRequest(agent="codex", evaluation_type="schematic", prompt="test")
    assert request.schematic_task_type == "block_to_schematic"


def test_schematic_task_resolves_its_own_skills_and_evaluator(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes_eval.load_runtime_settings",
        lambda root: {"schematic_task_profiles": {
            "block_to_signal_list": {
                "skills": ["signal-interface-generation"],
                "evaluator_id": "signal-list-evaluator",
            },
        }},
    )
    request = RunRequest(
        agent="codex",
        evaluation_type="schematic",
        schematic_task_type="block_to_signal_list",
        prompt="test",
    )

    resolved = _apply_schematic_skill_settings(request)

    assert resolved.skills == ["signal-interface-generation"]
    assert resolved.evaluator_id == "signal-list-evaluator"


def test_run_rejects_an_uninstalled_evaluator_before_queueing():
    response = client.post("/api/run", json={
        "agent": "codex",
        "skill": "example-marker",
        "prompt": "test",
        "evaluator_id": "not-installed",
    })
    assert response.status_code == 400
    assert "not installed" in response.json()["detail"]


def test_skill_files_can_be_read_without_escaping_the_skill_root():
    response = client.get("/api/skills/example-marker/files/SKILL.md")
    assert response.status_code == 200
    assert response.json()["kind"] == "text"
    assert "example-marker" in response.json()["content"]

    escaped = client.get("/api/skills/example-marker/files/../config/models.yaml")
    assert escaped.status_code in {400, 404}


def test_skill_delete_requires_confirmation_and_removes_folder(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    target = skills_root / "temporary-skill"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: temporary-skill\ndescription: test\n---\n", encoding="utf-8")
    monkeypatch.setattr("app.skill_registry.SKILLS_ROOT", skills_root)

    rejected = client.request(
        "DELETE", "/api/skills/temporary-skill", json={"confirm": False}
    )
    assert rejected.status_code == 400
    assert target.is_dir()

    response = client.request(
        "DELETE", "/api/skills/temporary-skill", json={"confirm": True}
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert not target.exists()


def test_run_interactions_and_open_folder_are_scoped_to_result_dir(tmp_path, monkeypatch):
    run_dir = tmp_path / "local" / "schematic" / "20260908-120000__run-safe"
    run_dir.mkdir(parents=True)
    trace = run_dir / "model-interactions.json"
    trace.write_text('[{"request_id":"req-1","status":"success"}]', encoding="utf-8")
    (run_dir / "evaluation-report.json").write_text(
        '{"run_id":"run-safe","database_trace_file":"' + str(trace).replace("\\", "\\\\") + '"}',
        encoding="utf-8",
    )
    opened = []
    monkeypatch.setattr("app.api.routes_runs.RUNS_ROOT", tmp_path)
    monkeypatch.setattr("app.api.routes_runs.os.startfile", lambda path: opened.append(path))

    response = client.get("/api/runs/run-safe/interactions")
    assert response.status_code == 200
    assert response.json()["items"][0]["request_id"] == "req-1"
    assert response.json()["total"] == 1
    assert response.json()["page"] == 1
    assert response.json()["summary"]["interaction_count"] == 1
    assert client.get("/api/runs/run-safe/interactions", params={"search": "missing"}).json()["total"] == 0
    response = client.post("/api/runs/run-safe/open-folder")
    assert response.status_code == 200
    assert opened == [str(run_dir.resolve())]


def test_run_interactions_can_filter_main_and_subagent_calls(tmp_path, monkeypatch):
    run_dir = tmp_path / "local" / "schematic" / "20260908-120000__run-actors"
    run_dir.mkdir(parents=True)
    trace = run_dir / "model-interactions.json"
    trace.write_text(
        '[{"request_id":"main","proxy_server_request":{"messages":[{"role":"user","content":"主任务"}]}},'
        '{"request_id":"child","proxy_server_request":{"messages":[{"role":"user","content":"[Subagent Context] child\\n[Subagent Task]\\n处理 slice_id=LIGHT_SENSE"}]}}]',
        encoding="utf-8",
    )
    (run_dir / "evaluation-report.json").write_text(
        '{"run_id":"run-actors","database_trace_file":"' + str(trace).replace("\\", "\\\\") + '"}',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.api.routes_runs.RUNS_ROOT", tmp_path)

    main = client.get("/api/runs/run-actors/interactions", params={"scope": "main_agent"}).json()
    child = client.get("/api/runs/run-actors/interactions", params={"scope": "subagent"}).json()

    assert [item["request_id"] for item in main["items"]] == ["main"]
    assert [item["request_id"] for item in child["items"]] == ["child"]
    assert child["items"][0]["subagent_name"] == "LIGHT_SENSE"
    assert child["subagents"][0]["name"] == "LIGHT_SENSE"
    assert child["scope_counts"] == {"all": 2, "main_agent": 1, "subagent": 1}


def test_batch_rejects_duplicate_combinations_before_queueing():
    response = client.post(
        "/api/batches",
        json={
            "name": "duplicates",
            "targets": [
                {"agent": "codex", "model": "gpt-5.4", "profile": "native_codex"},
                {"agent": "codex", "model": "gpt-5.4", "profile": "native_codex"},
            ],
            "base_request": {"skill": "example-marker", "prompt": "test"},
        },
    )
    assert response.status_code == 400
    assert "unique" in response.json()["detail"]


def test_batch_queues_unique_agent_model_combinations(monkeypatch):
    captured = {}

    def fake_submit(requests, skill_dir, *, name):
        captured.update(requests=requests, skill_dir=skill_dir, name=name)
        return {"batch_id": "batch-test", "total_jobs": len(requests)}

    monkeypatch.setattr("app.api.routes_eval.job_manager.submit_batch", fake_submit)
    response = client.post(
        "/api/batches",
        json={
            "name": "matrix",
            "targets": [
                {"agent": "codex", "model": "gpt-5.4", "profile": "native_codex"},
                {"agent": "codex", "model": "gpt-5.5", "profile": "native_codex"},
            ],
            "base_request": {"skill": "example-marker", "prompt": "test"},
        },
    )
    assert response.status_code == 200
    assert response.json() == {"batch_id": "batch-test", "total_jobs": 2}
    assert captured["name"] == "matrix"
    assert [item["model"] for item in captured["requests"]] == ["gpt-5.4", "gpt-5.5"]


def test_model_profile_api_keeps_api_keys_out_of_responses(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "models.yaml").write_text(
        "default_profile: native\nprofiles:\n  native:\n    type: native\n    model: gpt-test\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.api.routes_skill.BACKEND_ROOT", tmp_path)

    response = client.put(
        "/api/model-profiles/custom_gateway",
        json={
            "model": "provider/model",
            "api_base": "https://gateway.example/v1",
            "api_key_env": "CUSTOM_GATEWAY_KEY",
            "api_key": "api-secret",
            "protocol": "openai_compatible",
            "agent_models": {"claude": "sonnet"},
        },
    )

    assert response.status_code == 200
    assert response.json()["supports_all_evaluation_agents"] is True
    assert "api-secret" not in response.text
    listing = client.get("/api/model-profiles")
    assert listing.status_code == 200
    assert "api-secret" not in listing.text
    assert client.delete("/api/model-profiles/custom_gateway").json()["removed"] is True
