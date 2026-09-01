from fastapi.testclient import TestClient

from app.main import app


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


def test_built_frontend_is_served_when_dist_exists():
    response = client.get("/")
    if response.status_code == 200:
        assert "<div id=\"app\"></div>" in response.text


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
