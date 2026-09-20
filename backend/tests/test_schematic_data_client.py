from fastapi.testclient import TestClient

from app.main import app
from app.schematic_data_client import SchematicDataClient, _records


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


def test_schematic_data_query_does_not_require_login(monkeypatch):
    class FakeClient:
        def query_payload(self, **kwargs):
            return {"data": {"records": [], "total": 0}}

    monkeypatch.setattr("app.api.routes_schematic_data.SchematicDataClient", FakeClient)
    response = TestClient(app).get("/api/schematic-data/query")
    assert response.status_code == 200


def test_schematic_data_client_sends_configured_cookie(monkeypatch):
    captured = {}

    class Settings:
        schematic_data_api_base_url = "http://java.internal:8080"
        schematic_data_query_path = "/schematic/schematicData/query"
        schematic_data_api_cookie = "JSESSIONID=session-secret; tenant=intranet"
        schematic_data_timeout_seconds = 15
        schematic_data_query_page_size = 20
        cache_default_ttl_seconds = 300

    class Response:
        def raise_for_status(self): return None
        def json(self): return {"data": {"records": [], "total": 0}}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("app.schematic_data_client.load_infrastructure_settings", lambda: Settings())
    monkeypatch.setattr("app.schematic_data_client.httpx.get", fake_get)

    payload = SchematicDataClient().query_payload(
        collection_name="HDschematicRationalityCollection",
        page=1,
        size=20,
        use_cache=False,
    )

    assert payload["data"]["total"] == 0
    assert captured["headers"] == {
        "Cookie": "JSESSIONID=session-secret; tenant=intranet"
    }
