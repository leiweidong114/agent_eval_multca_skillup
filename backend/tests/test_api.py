from fastapi.testclient import TestClient

from app.main import app
from app.api.routes_eval import RunRequest


client = TestClient(app)


def test_health_and_discovery_endpoints():
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
    assert model_config.json()["llm_judge"]["profile"] == "litellm_glm_4_7"
    assert model_config.json()["llm_judge"]["model"] == "glm-4.7"


def test_database_health_never_exposes_credentials_or_crashes():
    response = client.get("/api/database/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "error", "disabled"}
    assert "password" not in payload
    assert "database_url" not in payload


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


def test_skill_files_can_be_read_without_escaping_the_skill_root():
    response = client.get("/api/skills/example-marker/files/SKILL.md")
    assert response.status_code == 200
    assert response.json()["kind"] == "text"
    assert "example-marker" in response.json()["content"]

    escaped = client.get("/api/skills/example-marker/files/../config/models.yaml")
    assert escaped.status_code in {400, 404}


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
