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
