from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from maeval.webapp.api import create_app
from agent_eval.model_config import ResolvedModelProfile
from maeval.webapp.benchmarks import seed_catalog
from maeval.webapp.db import Database
from scripts.import_model_eval_benchmarks import import_installed_official_benchmarks


def test_trusted_local_model_eval_supports_benchmark_workflow(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "model-eval", trusted_local=True)
    with TestClient(app) as client:
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "admin"

        catalog = client.get("/api/benchmarks")
        assert catalog.status_code == 200
        assert any(item["id"] == "repo-repair" for item in catalog.json())

        imported = client.post(
            "/api/benchmarks/import",
            json={
                "id": "integration-smoke",
                "name": "Integration Smoke",
                "description": "Question bank integration smoke test",
                "items": [
                    {
                        "key": "q1",
                        "prompt": "Answer with OK",
                        "expected": "OK",
                        "scorer": "exact",
                    }
                ],
            },
        )
        assert imported.status_code == 200
        assert imported.json()["item_count"] == 1

        benchmark_id = imported.json()["id"]
        items = client.get(f"/api/benchmarks/{benchmark_id}/items")
        assert items.status_code == 200
        assert items.json()[0]["prompt"] == "Answer with OK"


def test_prism_static_assets_use_mounted_prefix(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "model-eval", trusted_local=True)
    with TestClient(app) as client:
        html = client.get("/").text
        javascript = client.get("/assets/app.js").text
        assert "/prism/assets/app.js" in html
        assert "const API_BASE='/prism/api'" in javascript


def test_public_benchmark_migration_excludes_platform_data(tmp_path: Path) -> None:
    source_path = tmp_path / "legacy" / "maeval.db"
    source = Database(source_path)
    seed_catalog(source)
    source.execute(
        "UPDATE benchmarks SET status='installed',item_count=1 WHERE id='gsm8k'"
    )
    source.execute(
        """INSERT INTO benchmark_items(
        benchmark_id,item_key,category,prompt,expected_json,scorer_type,access_level)
        VALUES('gsm8k','sample','math','1+1','\"2\"','numeric_answer','private')"""
    )

    target_dir = tmp_path / "target"
    result = import_installed_official_benchmarks(source_path, target_dir)
    target = Database(target_dir / "maeval.db")

    assert result["benchmark_count"] == 1
    assert result["item_count"] == 1
    assert target.row("SELECT item_count FROM benchmarks WHERE id='gsm8k'")["item_count"] == 1
    assert target.row("SELECT COUNT(*) n FROM providers")["n"] == 0
    assert target.row("SELECT COUNT(*) n FROM experiments")["n"] == 0


def test_unified_ui_can_create_server_managed_litellm_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "maeval.webapp.api.resolve_model_profile",
        lambda *args, **kwargs: ResolvedModelProfile(
            name="test-profile",
            model="gateway/model-a",
            api_base="http://litellm.local/v1",
            environment={
                "LITELLM_API_KEY": "secret",
                "OPENAI_BASE_URL": "http://litellm.local/v1",
            },
            agent_args=("-c", "test=true"),
        ),
    )
    app = create_app(data_dir=tmp_path / "model-eval", trusted_local=True)
    with TestClient(app) as client:
        response = client.post(
            "/api/providers/auto",
            json={
                "agent": "codex",
                "model": "gateway/model-a",
                "profile": "test-profile",
                "task_kind": "direct",
            },
        )
        assert response.status_code == 200
        assert response.json()["kind"] == "codex_cli_direct"
        assert response.json()["model"] == "gateway/model-a"
