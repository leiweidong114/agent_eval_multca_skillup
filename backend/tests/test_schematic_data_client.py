from fastapi.testclient import TestClient

from app.main import app
from app.schematic_data_client import _records


def test_records_accepts_single_document():
    rows, total = _records({"sessionId": "s-1", "status": "completed"})
    assert rows == [{"sessionId": "s-1", "status": "completed"}]
    assert total is None


def test_records_accepts_nested_page():
    rows, total = _records({"data": {"records": [{"sessionId": "s-1"}], "total": 7}})
    assert rows == [{"sessionId": "s-1"}]
    assert total == 7


def test_schematic_data_query_returns_java_payload_unchanged(monkeypatch):
    payload = {
        "data": {
            "records": [{"sessionId": "session-1", "resultText": "{\"score\":86}"}],
            "total": 1,
        }
    }
    captured = {}

    class FakeClient:
        def query_payload(self, **kwargs):
            captured.update(kwargs)
            return payload

    monkeypatch.setattr("app.api.routes_schematic_data.SchematicDataClient", FakeClient)
    client = TestClient(app)
    assert client.post(
        "/api/auth/login", json={"employee_no": "test-worker", "password": "ignored"}
    ).status_code == 200

    response = client.get(
        "/api/schematic-data/query",
        params={
            "collectionName": "HDschematicRationalityCollection",
            "page": 2,
            "size": 50,
            "refresh": "true",
        },
    )

    assert response.status_code == 200
    assert response.json() == payload
    assert captured == {
        "collection_name": "HDschematicRationalityCollection",
        "page": 2,
        "size": 50,
        "use_cache": False,
    }


def test_schematic_data_query_requires_login():
    response = TestClient(app).get("/api/schematic-data/query")
    assert response.status_code == 401
