import json
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient

from app.main import app


BACKEND = Path(__file__).resolve().parents[1]
SKILL = BACKEND / "skills" / "schematic-generation"


def test_schematic_pipeline_and_judge(tmp_path):
    source = SKILL / "assets" / "example_block_diagram.json"
    output = tmp_path / "generated"
    subprocess.run([sys.executable, str(SKILL / "scripts" / "schematic_pipeline.py"), "--input", str(source), "--output", str(output)], check=True)
    report = subprocess.run([sys.executable, str(SKILL / "scripts" / "schematic_judge.py"), "--input", str(source), "--output", str(output)], check=True, capture_output=True, text=True)
    result = json.loads(report.stdout)
    assert result["score"] == 100
    assert len(json.loads((output / "schematic.json").read_text(encoding="utf-8"))["components"]) == 6
    events = json.loads((output / "events.json").read_text(encoding="utf-8"))
    assert len([event for event in events if event["event"] == "component_started"]) == 6
    assert len([event for event in events if event["event"] == "component_finished"]) == 6


def test_schematic_api_returns_openable_project_url():
    client = TestClient(app)
    client.post("/api/auth/login", json={"employee_no": "schematic-user", "password": "x"})
    diagram = client.get("/api/schematic/example").json()
    response = client.post("/api/schematic/generate", json=diagram)
    assert response.status_code == 200, response.text
    generated = response.json()
    assert generated["judge"]["score"] == 100
    project = client.get(f"/api/schematic/projects/{generated['project_id']}")
    assert project.status_code == 200
    assert project.json()["schematic"]["schema"] == "tianshu-schematic/v1"
    judged = client.post("/api/schematic/judge", json={"diagram": diagram, "schematic": generated["schematic"]})
    assert judged.status_code == 200
    assert judged.json()["score"] == 100


def test_schematic_interaction_search_uses_logged_in_employee(monkeypatch):
    client = TestClient(app)
    assert client.get("/api/schematic/interactions").status_code == 401
    client.post("/api/auth/login", json={"employee_no": "E20002", "password": "x"})
    captured = {}
    def fake_search(_root, **kwargs):
        captured.update(kwargs)
        return {"status": "ok", "interactions": [], "sessions": []}
    monkeypatch.setattr("app.api.routes_schematic.search_conversation_interactions", fake_search)
    response = client.get("/api/schematic/interactions")
    assert response.status_code == 200
    assert captured["user_id"] == "E20002"
