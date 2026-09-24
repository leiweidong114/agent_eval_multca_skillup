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
        "session_id": None,
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
        schematic_data_write_path = "/schematic/schematicData/insert"
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

    client = SchematicDataClient()
    payload = client.query_payload(
        collection_name="HDschematicRationalityCollection",
        page=1,
        size=20,
        use_cache=False,
    )

    assert payload["data"]["total"] == 0
    assert captured["headers"] == {
        "Cookie": "JSESSIONID=session-secret; tenant=intranet"
    }
    assert captured["verify"] is False
    assert client.query_diagnostics()[0]["status"] == "success"
    assert client.query_diagnostics()[0]["records_returned"] == 0


def test_schematic_data_client_inserts_record_with_cookie(monkeypatch):
    captured = {}

    class Settings:
        schematic_data_api_base_url = "http://java.internal:8080"
        schematic_data_query_path = "/schematic/schematicData/query"
        schematic_data_write_path = "/schematic/schematicData/insert"
        schematic_data_api_cookie = "JSESSIONID=session-secret"
        schematic_data_timeout_seconds = 15
        schematic_data_query_page_size = 20
        cache_default_ttl_seconds = 300

    class Response:
        def raise_for_status(self): return None
        def json(self): return {"status": "inserted", "record": {"_id": "mongo-1"}}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("app.schematic_data_client.load_infrastructure_settings", lambda: Settings())
    monkeypatch.setattr("app.schematic_data_client.httpx.post", fake_post)
    monkeypatch.setattr("app.schematic_data_client.clear_response_cache", lambda: captured.setdefault("cache_cleared", True))

    client = SchematicDataClient()
    result = client.insert_record(
        {"sessionId": "session-1", "resultText": "{}"}
    )

    assert result["record"]["_id"] == "mongo-1"
    assert captured["url"].endswith("/schematic/schematicData/insert")
    assert captured["headers"] == {"Cookie": "JSESSIONID=session-secret"}
    assert captured["verify"] is False
    assert captured["json"]["collectionName"] == "HDschematicRationalityCollection"
    assert captured["json"]["documents"] == [{"sessionId": "session-1", "resultText": "{}"}]
    assert captured["cache_cleared"] is True
    assert client.write_diagnostics()[0]["status"] == "success"
    assert client.write_diagnostics()[0]["session_id"] == "session-1"


def test_schematic_data_insert_route_needs_no_login_and_forwards_payload(monkeypatch):
    captured = {}

    class FakeClient:
        def insert_documents(self, documents, *, collection_name):
            captured["documents"] = documents
            captured["collection_name"] = collection_name
            return {"status": "inserted", "documents": [{"_id": "mongo-1", **documents[0]}]}

    monkeypatch.setattr("app.api.routes_schematic_data.SchematicDataClient", FakeClient)
    client = TestClient(app)
    payload = {
        "collectionName": "HDschematicRationalityCollection",
        "documents": [{
            "uuid": "uuid-1", "status": "completed", "createUser": "100001",
            "createTime": "2026-09-20T08:00:00Z", "checkType": "hscope_diagram_lint",
            "checkMessage": "测试", "userName": "测试用户", "hscopeProjectId": "project-1",
            "boardNum": "BOARD-1", "sessionId": "session-1", "resultText": "{}",
        }],
    }

    response = client.post("/api/schematic-data/insert", json=payload)

    assert response.status_code == 201
    assert response.json()["documents"][0]["_id"] == "mongo-1"
    assert captured["documents"] == payload["documents"]
    assert captured["collection_name"] == "HDschematicRationalityCollection"


def test_schematic_data_insert_route_accepts_empty_metric_result_text(monkeypatch):
    captured = {}

    class FakeClient:
        def insert_documents(self, documents, *, collection_name):
            captured["documents"] = documents
            return {"status": "inserted", "documents": documents}

    monkeypatch.setattr("app.api.routes_schematic_data.SchematicDataClient", FakeClient)
    payload = {
        "collectionName": "HDschematicRationalityCollection",
        "documents": [{
            "uuid": "metric-uuid", "status": "completed", "createUser": "agent-eval",
            "createTime": "2026-09-24T08:00:00Z", "checkType": "agent_eval_metric",
            "checkMessage": "指标计算结果", "userName": "Agent Eval", "hscopeProjectId": "session-metrics",
            "boardNum": "BOARD-1", "sessionId": "session-1", "resultText": "",
        }],
    }

    response = TestClient(app).post("/api/schematic-data/insert", json=payload)

    assert response.status_code == 201
    assert captured["documents"][0]["resultText"] == ""


def test_update_record_uses_put_and_requires_existing_match(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "updated", "matchedCount": 1}

    def fake_put(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.schematic_data_client.httpx.put", fake_put)
    monkeypatch.setattr("app.schematic_data_client.clear_response_cache", lambda: None)
    client = SchematicDataClient()
    response = client.update_record(session_id="session-1", check_type="hscope_diagram_lint",
                                    field="agentEvalMetrics", value={"status": "completed"})
    assert response["matchedCount"] == 1
    assert captured["url"].endswith("/schematic/schematicData/update")
    assert captured["params"]["sessionId"] == "session-1"
    assert captured["params"]["checkType"] == "hscope_diagram_lint"
    assert "uuid" not in captured["params"]
    assert captured["json"]["field"] == "agentEvalMetrics"


def test_aggregate_uses_dedicated_atomic_java_put(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "updated", "matchedCount": 1}

    def fake_put(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.schematic_data_client.httpx.put", fake_put)
    monkeypatch.setattr("app.schematic_data_client.clear_response_cache", lambda: None)
    client = SchematicDataClient()
    result = client.upsert_aggregate_metrics(value={"rates": {"语料覆盖率": "75%"}})
    assert result["matchedCount"] == 1
    assert captured["url"].endswith("/schematic/schematicData/aggregate")
    assert captured["params"] == {"collectionName": "HDschematicRationalityCollection"}
    assert captured["json"]["rates"]["语料覆盖率"] == "75%"


def test_delete_records_sends_id_in_json_body_and_treats_missing_as_idempotent(monkeypatch):
    captured = {}

    class Response:
        status_code = 404

        def raise_for_status(self):
            raise AssertionError("404 replacement delete must not raise")

    def fake_post(url, **kwargs):
        captured.update(method="POST", url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.schematic_data_client.httpx.post", fake_post)
    monkeypatch.setattr("app.schematic_data_client.clear_response_cache", lambda: None)
    result = SchematicDataClient().delete_records(record_id="68cec0000000000000000001")
    assert result == {"status": "not_found", "deletedCount": 0}
    assert captured["method"] == "POST"
    assert captured["json"] == {
        "collectionName": "HDschematicRationalityCollection",
        "_id": "68cec0000000000000000001",
    }
    assert captured["url"].endswith("/schematic/schematicData/delete")


def test_schematic_data_delete_route_only_accepts_mongo_id(monkeypatch):
    captured = {}

    class FakeClient:
        def delete_records(self, *, record_id, collection_name):
            captured.update(record_id=record_id, collection_name=collection_name)
            return {"status": "deleted", "deletedCount": 1}

    monkeypatch.setattr("app.api.routes_schematic_data.SchematicDataClient", FakeClient)
    response = TestClient(app).post(
        "/api/schematic-data/delete",
        json={
            "collectionName": "HDschematicRationalityCollection",
            "_id": "68cec0000000000000000001",
        },
    )

    assert response.status_code == 200
    assert response.json()["deletedCount"] == 1
    assert captured == {
        "record_id": "68cec0000000000000000001",
        "collection_name": "HDschematicRationalityCollection",
    }

    invalid = TestClient(app).post(
        "/api/schematic-data/delete",
        json={"collectionName": "HDschematicRationalityCollection", "sessionId": "session-1"},
    )
    assert invalid.status_code == 422


def test_schematic_data_insert_with_trailing_slash_needs_no_login(monkeypatch):
    class FakeClient:
        def insert_documents(self, documents, *, collection_name):
            return {"status": "inserted", "documents": documents}

    monkeypatch.setattr("app.api.routes_schematic_data.SchematicDataClient", FakeClient)
    payload = {
        "collectionName": "HDschematicRationalityCollection",
        "documents": [{
            "uuid": "uuid-2", "status": "completed", "createUser": "100001",
            "createTime": "2026-09-21T08:00:00Z", "checkType": "hscope_diagram_lint",
            "checkMessage": "测试", "userName": "测试用户", "hscopeProjectId": "project-2",
            "boardNum": "BOARD-2", "sessionId": "session-2", "resultText": "{}",
        }],
    }

    response = TestClient(app).post("/api/schematic-data/insert/", json=payload)

    assert response.status_code == 201
