import os
import json
from pathlib import Path
import shutil
import sys
import time

from fastapi.testclient import TestClient

from app.main import app
from app.api.routes_eval import RunRequest, _apply_schematic_skill_settings
from app.api.routes_skill import _cli_subprocess_environment


BACKEND = Path(__file__).resolve().parents[1]
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


def test_openapi_schema_is_available():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/api/runs" in response.json()["paths"]


def test_cli_subprocess_environment_includes_current_source_tree(monkeypatch):
    stale_source = str(BACKEND.parent / "stale-copy" / "backend" / "src")
    monkeypatch.setenv("PYTHONPATH", stale_source)

    environment = _cli_subprocess_environment()
    entries = environment["PYTHONPATH"].split(os.pathsep)

    assert entries[0] == str(BACKEND / "src")
    assert entries[1] == str(BACKEND)
    assert entries[2] == stale_source


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


def test_justdo_http_settings_hide_token(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.routes_skill.BACKEND_ROOT", tmp_path)
    saved = client.put(
        "/api/agents/justdo/http",
        json={"url": "http://192.168.1.20:43128", "token": "test-private-token"},
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "url": "http://192.168.1.20:43128",
        "token_configured": True,
        "enabled": True,
    }
    loaded = client.get("/api/agents/justdo/http")
    assert loaded.json() == saved.json()
    assert "test-private-token" not in loaded.text
    assert "JUSTDO_HTTP_TOKEN=test-private-token" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_justdo_http_settings_require_token(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.routes_skill.BACKEND_ROOT", tmp_path)
    response = client.put(
        "/api/agents/justdo/http",
        json={"url": "http://192.168.1.20:43128"},
    )
    assert response.status_code == 400


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
    bundled_source = BACKEND / "evaluator_plugins" / "schematic-default"
    bundled_target = tmp_path / "backend" / "evaluator_plugins" / "schematic-default"
    bundled_target.parent.mkdir(parents=True)
    shutil.copytree(bundled_source, bundled_target)
    (config / "models.yaml").write_text(
        "litellm:\n  model: fallback-model\n  api_base: https://gateway.example/v1\n  api_key_env: LITELLM_API_KEY\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.api.routes_skill.BACKEND_ROOT", tmp_path)
    response = client.put(
        "/api/settings",
        json={"judge_model": "judge-model", "agent_test_model": "agent-model"},
    )

    assert response.status_code == 200, response.text
    assert client.get("/api/settings").json()["judge_model"] == "judge-model"
    saved = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "judge-model" in saved
    assert "api_key" not in saved.lower()


def test_results_root_setting_changes_new_output_without_hiding_old_root(tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    backend.mkdir()
    selected = tmp_path / "short-runs"
    monkeypatch.setattr("app.api.routes_skill.BACKEND_ROOT", backend)
    monkeypatch.setattr("app.api.routes_skill.validate_results_root", lambda value: selected.resolve())

    response = client.put("/api/settings/results-root", json={"path": str(selected)})

    assert response.status_code == 200, response.text
    assert response.json()["path"] == str(selected.resolve())
    assert str((backend / "evaluation_results").resolve()) in response.json()["readable_roots"]
    assert client.get("/api/settings/results-root").json()["configured"] is True
    assert "AGENT_EVAL_RESULTS_ROOT=" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_results_list_and_detail_keep_old_root_after_switch(tmp_path, monkeypatch):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    run_dir = old_root / "test-worker" / "demo" / "20260921-120000__run-old"
    run_dir.mkdir(parents=True)
    (run_dir / "evaluation-report.json").write_text(
        json.dumps({"run_id": "run-old", "user_id": "test-worker", "status": "completed"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.api.routes_runs.readable_runs_roots", lambda: (new_root, old_root))

    assert any(item["run_id"] == "run-old" for item in client.get("/api/runs", params={"summary_only": True}).json())
    assert client.get("/api/runs/run-old").json()["run_id"] == "run-old"


def test_batch_model_probe_endpoint_returns_real_probe_summary(monkeypatch):
    captured = {}

    def fake_refresh(root, **kwargs):
        captured.update(kwargs)
        return {
            "connectivity_tested": True,
            "available_model_count": 2,
            "unavailable_model_count": 1,
            "models": [{"id": "a", "available": True}, {"id": "b", "available": True}],
            "unavailable_models": [{"id": "c", "available": False}],
        }

    monkeypatch.setattr(
        "app.api.routes_skill.refresh_litellm_model_catalog",
        fake_refresh,
    )
    response = client.post(
        "/api/models/test-batch",
        json={"workers": 4, "timeout_seconds": 12},
    )

    assert response.status_code == 200
    assert response.json()["available_model_count"] == 2
    assert captured["employee_no"] == "test-worker"


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


def test_run_request_disables_legacy_without_skill_benchmark():
    request = RunRequest(
        agent="codex",
        skill="example-marker",
        prompt="test",
        benchmark=True,
    )

    assert request.benchmark is False


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


def test_evaluation_input_upload_is_owned_and_resolvable(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.routes_eval.runs_root", lambda: tmp_path)
    monkeypatch.setattr("app.api.routes_eval.readable_runs_roots", lambda: (tmp_path,))

    uploaded = client.post(
        "/api/evaluation-inputs",
        files={"file": ("signals.json", b'{"signals":["UART_TX"]}', "application/json")},
    )

    assert uploaded.status_code == 200, uploaded.text
    payload = uploaded.json()
    assert payload["filename"] == "signals.json"
    assert payload["size"] > 0
    request = RunRequest(
        agent="codex",
        skill="example-marker",
        prompt="process the uploaded signal list",
        input_files=[{
            "upload_id": payload["upload_id"],
            "filename": payload["filename"],
        }],
    )
    from app.api.routes_eval import _run_payload

    resolved = _run_payload(request, "test-worker")
    assert resolved["input_file_paths"][0]["filename"] == "signals.json"
    assert Path(resolved["input_file_paths"][0]["source_path"]).read_bytes().startswith(b"{")


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
    monkeypatch.setattr("app.api.routes_runs.readable_runs_roots", lambda: (tmp_path,))
    if sys.platform == "win32":
        monkeypatch.setattr("app.api.routes_runs.os.startfile", lambda path: opened.append(path))
    else:
        monkeypatch.setattr(
            "app.api.routes_runs.subprocess.Popen",
            lambda command: opened.append(command[-1]),
        )

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
    monkeypatch.setattr("app.api.routes_runs.readable_runs_roots", lambda: (tmp_path,))

    main = client.get("/api/runs/run-actors/interactions", params={"scope": "main_agent"}).json()
    child = client.get("/api/runs/run-actors/interactions", params={"scope": "subagent"}).json()

    assert [item["request_id"] for item in main["items"]] == ["main"]
    assert [item["request_id"] for item in child["items"]] == ["child"]
    assert child["items"][0]["subagent_name"] == "LIGHT_SENSE"
    assert child["subagents"][0]["name"] == "LIGHT_SENSE"
    assert child["scope_counts"] == {"all": 2, "main_agent": 1, "subagent": 1}


def test_run_summary_can_include_machine_local_cli_results(tmp_path, monkeypatch):
    run_dir = tmp_path / "local" / "JustDo-CLI" / "20260915-120000__local-run"
    run_dir.mkdir(parents=True)
    (run_dir / "evaluation-report.json").write_text(
        '{"run_id":"local-run","user_id":"local","task_name":"JustDo CLI",'
        '"status":"completed","agent":"justdo","provider_model":"judge-model",'
        '"evaluation_type":"schematic","skills":["schematic-pipeline"],'
        '"started_at":"2026-09-15T12:00:00+00:00","scores":{"overall_score":99}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.api.routes_runs.readable_runs_roots", lambda: (tmp_path,))
    monkeypatch.setattr("app.api.routes_runs.get_cached_json", lambda key: None)
    monkeypatch.setattr("app.api.routes_runs.set_cached_json", lambda *args, **kwargs: None)

    hidden = client.get("/api/runs", params={"summary_only": True}).json()
    visible = client.get(
        "/api/runs", params={"summary_only": True, "include_local": True}
    ).json()

    assert hidden == []
    assert visible[0]["run_id"] == "local-run"
    assert visible[0]["score"] == 99
    assert "report" not in visible[0]


def test_judge_interactions_are_listed_as_summaries_and_opened_separately(tmp_path, monkeypatch):
    audit_root = tmp_path / "_judge"
    records = audit_root / "records"
    records.mkdir(parents=True)
    interaction_id = "a" * 32
    summary = {
        "interaction_id": interaction_id,
        "user_id": "local",
        "purpose": "evaluation_judge",
        "model": "judge-model",
        "status": "success",
        "started_at": "2026-09-15T12:00:00+00:00",
        "usage": {"total_tokens": 42},
    }
    (audit_root / "index.jsonl").write_text(json.dumps(summary) + "\n", encoding="utf-8")
    (records / f"{interaction_id}.json").write_text(
        json.dumps({**summary, "input": {"system": [], "user": []}, "output": {"content": "ok"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.api.routes_judge.readable_runs_roots", lambda: (tmp_path,))

    listed = client.get("/api/judge-interactions").json()
    filtered = client.get(
        "/api/judge-interactions",
        params={"judge_type": "task_evaluation", "status": "success"},
    ).json()
    filters = client.get("/api/judge-interactions/filters").json()
    detail = client.get(f"/api/judge-interactions/{interaction_id}").json()

    assert listed["items"][0]["total_tokens"] == 42
    assert listed["items"][0]["judge_type"] == "task_evaluation"
    assert "input" not in listed["items"][0]
    assert filtered["total"] == 1
    assert filters["judge_types"] == ["task_evaluation"]
    assert detail["output"]["content"] == "ok"


def test_judge_availability_uses_session_metric_production_paths(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes_judge.classify_session_task",
        lambda conversation, employee_no=None: {
            "status": "completed",
            "task_type": "block_to_schematic",
            "model": "judge-model",
            "usage": {"total_tokens": 12},
        },
    )
    monkeypatch.setattr(
        "app.api.routes_judge.judge_session_metrics",
        lambda conversation, employee_no=None: {
            "status": "completed",
            "models": ["judge-model"],
            "chunks_total": 1,
            "chunks_completed": 1,
            "usage": {"total_tokens": 18},
        },
    )

    response = client.post("/api/judge-interactions/test")

    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is True
    assert result["checks"]["task_classification"]["task_type"] == "block_to_schematic"
    assert result["checks"]["session_metric_judge"]["chunks_completed"] == 1


def test_judge_availability_job_reports_steps_without_blocking_request(monkeypatch):
    def classify(conversation, *, employee_no=None, progress_callback=None):
        assert employee_no == "test-worker"
        progress_callback("model_resolved", {"model": "judge-model"})
        progress_callback("request_started", {"attempt": 1, "max_attempts": 4, "timeout_seconds": 120})
        progress_callback("response_received", {"attempt": 1, "status_code": 200, "duration_ms": 8})
        return {"status": "completed", "model": "judge-model", "task_type": "block_to_schematic"}

    def metric(conversation, *, employee_no=None, progress_callback=None, request_progress_callback=None):
        progress_callback("llm_judge_chunk_started", 1, 1, "开始分片")
        request_progress_callback("request_started", {"attempt": 1, "max_attempts": 4})
        request_progress_callback("retry_wait", {"attempt": 1, "wait_seconds": 0.5, "status_code": 429})
        progress_callback("llm_judge_chunk_completed", 1, 1, "完成分片")
        return {"status": "completed", "models": ["judge-model"], "chunks_completed": 1}

    monkeypatch.setattr("app.api.routes_judge.classify_session_task", classify)
    monkeypatch.setattr("app.api.routes_judge.judge_session_metrics", metric)

    started = client.post("/api/judge-interactions/test-jobs")

    assert started.status_code == 202
    job_id = started.json()["job_id"]
    for _ in range(100):
        current = client.get(f"/api/judge-interactions/test-jobs/{job_id}")
        assert current.status_code == 200
        if current.json()["status"] not in {"queued", "running"}:
            break
        time.sleep(0.01)
    job = current.json()
    assert job["status"] == "completed"
    assert job["result"]["ok"] is True
    assert job["checks"]["task_classification"]["status"] == "completed"
    assert {event["phase"] for event in job["events"]} == {"setup", "classification", "metrics", "finish"}
    assert any(event["stage"] == "retry_wait" and event["details"]["status_code"] == 429 for event in job["events"])
    assert client.get("/api/judge-interactions/test-jobs/missing").status_code == 404


def test_historical_metric_sessions_filter_calculated_and_uncalculated(monkeypatch):
    captured = []

    class FakeMetricsStore:
        def all_statuses(self):
            return {
                "calculated-session": {
                    "status": "completed",
                    "calculated_at": "2026-09-21T00:00:00+00:00",
                    "metric_definition_version": "v1",
                }
            }

    def fake_search(root, **kwargs):
        captured.append(kwargs)
        session_id = (
            "calculated-session"
            if kwargs.get("allowed_root_session_ids") is not None
            else "uncalculated-session"
        )
        return {
            "status": "ok",
            "total": 1,
            "count": 1,
            "conversations": [{"root_session_id": session_id}],
        }

    monkeypatch.setattr("app.api.routes_metrics.MetricsStore", FakeMetricsStore)
    monkeypatch.setattr("app.api.routes_metrics.search_conversations", fake_search)
    monkeypatch.setattr("app.api.routes_metrics.get_cached_json", lambda key: None)
    monkeypatch.setattr("app.api.routes_metrics.set_cached_json", lambda *args, **kwargs: None)

    calculated = client.get(
        "/api/session-metrics/sessions", params={"metric_status": "calculated"}
    ).json()
    uncalculated = client.get(
        "/api/session-metrics/sessions", params={"metric_status": "uncalculated"}
    ).json()

    assert captured[0]["allowed_root_session_ids"] == {"calculated-session"}
    assert captured[0]["excluded_root_session_ids"] is None
    assert calculated["conversations"][0]["metric_status"] == "completed"
    assert captured[1]["allowed_root_session_ids"] is None
    assert captured[1]["excluded_root_session_ids"] == {"calculated-session"}
    assert uncalculated["conversations"][0]["metric_status"] == "not_calculated"


def test_completed_metric_process_can_be_loaded_by_session_id(monkeypatch):
    class FakeMetricsStore:
        def get_process_trace(self, session_id):
            return {
                "session_id": session_id,
                "job_id": "metrics-test",
                "status": "completed",
                "events": [{"stage": "session_completed", "session_id": session_id}],
            } if session_id == "completed-session" else None

    monkeypatch.setattr("app.api.routes_metrics.MetricsStore", FakeMetricsStore)
    result = client.get("/api/session-metrics/completed-session/process")
    missing = client.get("/api/session-metrics/missing-session/process")

    assert result.status_code == 200
    assert result.json()["events"][0]["stage"] == "session_completed"
    assert missing.status_code == 404


def test_quality_records_endpoint_returns_each_full_result_text(monkeypatch):
    long_report = "检查结果\n" * 1000

    class FakeMetricsStore:
        def quality_records(self, session_id):
            assert session_id == "session-4"
            return [{"_id": str(index), "checkType": kind, "resultText": long_report}
                    for index, kind in enumerate(("hscope_diagram_lint", "hscope_block_corpus_check",
                                                  "signal-interface-checker", "tianshu-drc-review"))]

    monkeypatch.setattr("app.api.routes_metrics.MetricsStore", FakeMetricsStore)
    response = client.get("/api/session-metrics/session-4/quality-records")
    assert response.status_code == 200
    assert response.json()["count"] == 4
    assert all(record["resultText"] == long_report for record in response.json()["records"])


def test_quality_aggregate_endpoint_reads_persisted_rollup(monkeypatch):
    class FakeMetricsStore:
        def get_quality_aggregate(self):
            return {"rates": {"语料覆盖率": "75.00%"}, "quality_session_count": 2}

    monkeypatch.setattr("app.api.routes_metrics.MetricsStore", FakeMetricsStore)
    response = client.get("/api/session-metrics/aggregate/quality")
    assert response.status_code == 200
    assert response.json()["rates"]["语料覆盖率"] == "75.00%"


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
    assert {item["user_id"] for item in captured["requests"]} == {"test-worker"}


def test_batch_cancel_endpoint_cancels_owned_batch(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes_eval.job_manager.get_batch",
        lambda batch_id: {"batch_id": batch_id, "user_id": "test-worker", "status": "running"},
    )
    monkeypatch.setattr(
        "app.api.routes_eval.job_manager.cancel_batch",
        lambda batch_id: {"batch_id": batch_id, "user_id": "test-worker", "status": "cancelling"},
    )

    response = client.post("/api/batches/batch-test/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelling"


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
