from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_and_discovery_endpoints():
    assert client.get("/api/health").json()["status"] == "ok"
    agents = client.get("/api/agents")
    assert agents.status_code == 200
    assert any(item["agent"] == "codex" for item in agents.json())
    skills = client.get("/api/skills")
    assert skills.status_code == 200


def test_database_health_never_exposes_credentials_or_crashes():
    response = client.get("/api/database/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "error", "disabled"}
    assert "password" not in payload
    assert "database_url" not in payload
