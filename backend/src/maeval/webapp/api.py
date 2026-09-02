from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from maeval.adapters import _run_process, get_adapter, resolve_executable
from maeval.models import Candidate, ScorerSpec, Task
from agent_eval.model_config import resolve_model_profile

from .benchmarks import install_benchmark, seed_catalog
from .auth import (
    create_session,
    ensure_bootstrap_admin,
    hash_password,
    public_user,
    revoke_session,
    session_user,
    verify_password,
)
from .db import Database, decode_json_fields, utcnow
from .engine import EvaluationManager, KIND_TO_ADAPTER
from .connectivity import test_provider_connection
from .health_monitor import ProviderHealthMonitor
from .protocol import SUITES, TRACKS, build_manifest, paired_comparison, select_items, summarize_results
from .reference_agent import PROTOCOL_VERSION
from .security import SecretBox


class ProviderIn(BaseModel):
    name: str = Field(min_length=1)
    kind: str
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    shared: bool = False


class AutoProviderIn(BaseModel):
    agent: str = Field(pattern=r"^(direct|codex)$")
    model: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    task_kind: str = Field(default="direct", pattern=r"^(direct|repo)$")


class ExperimentIn(BaseModel):
    name: str = Field(min_length=1)
    provider_ids: list[int] = Field(min_length=1)
    benchmark_ids: list[str] = Field(min_length=1)
    repeats: int = Field(default=1, ge=1, le=10)
    sample_limit: int | None = Field(default=None, ge=1)
    concurrency: int = Field(default=1, ge=1, le=16)
    allow_unsafe_code: bool = False
    track: str = "model_direct"
    random_seed: int = Field(default=42, ge=0, le=2_147_483_647)
    sampling_strategy: str = "stratified"
    budget: dict[str, Any] = Field(
        default_factory=lambda: {
            "timeout_seconds_per_task": 300,
            "max_output_tokens": 4096,
        }
    )


class BenchmarkItemIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    category: str = Field(default="custom", max_length=100)
    prompt: str = Field(min_length=1)
    expected: Any
    scorer: str = "exact"
    metadata: dict[str, Any] = Field(default_factory=dict)
    access_level: str = "private"


class BenchmarkImportIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)
    source_url: str | None = None
    license: str = "Internal"
    version: str = Field(default="1", min_length=1, max_length=100)
    task_type: str = Field(default="custom_qa", min_length=1, max_length=100)
    language: str = Field(default="en", min_length=1, max_length=30)
    visibility: str = "private"
    items: list[BenchmarkItemIn] = Field(min_length=1)
    replace: bool = False


class ItemTryIn(BaseModel):
    provider_id: int
    allow_unsafe_code: bool = False


class ItemAccessIn(BaseModel):
    access_level: str


class UserSettingsIn(BaseModel):
    health_check_enabled: bool = False
    health_check_interval_minutes: int = Field(default=60, ge=5, le=10080)
    theme: str = "forest"
    language: str = "zh-CN"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class UserIn(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=500)
    role: str = "evaluator"


class UserUpdateIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    role: str
    active: bool = True
    password: str | None = Field(default=None, min_length=10, max_length=500)


def create_app(
    data_dir: Path | None = None,
    *,
    trusted_local: bool = False,
) -> FastAPI:
    root = Path(__file__).resolve().parents[3]
    data = data_dir or root / "data"
    db, secrets = Database(data / "maeval.db"), SecretBox(data)
    seed_catalog(db)
    bootstrap_admin = ensure_bootstrap_admin(db, data)
    db.execute(
        "UPDATE providers SET owner_user_id=? WHERE owner_user_id IS NULL",
        (bootstrap_admin["id"],),
    )
    db.execute(
        "UPDATE experiments SET owner_user_id=? WHERE owner_user_id IS NULL",
        (bootstrap_admin["id"],),
    )
    db.execute(
        "UPDATE audit_events SET owner_user_id=? WHERE owner_user_id IS NULL",
        (bootstrap_admin["id"],),
    )
    db.execute(
        """UPDATE benchmarks SET owner_user_id=?,visibility='private'
        WHERE official=0 AND id<>'repo-repair' AND owner_user_id IS NULL""",
        (bootstrap_admin["id"],),
    )
    db.execute(
        "UPDATE benchmarks SET visibility='public',owner_user_id=NULL WHERE id='repo-repair'"
    )
    def recover_unfinished_experiments() -> None:
        """Mark stale runs only when an ASGI server actually starts.

        Keeping this out of application construction is important: test discovery,
        CLI imports, and administrative scripts may import this module while a
        different server process owns active evaluation threads.
        """
        unfinished = db.rows(
            "SELECT id,status,owner_user_id FROM experiments WHERE status IN ('queued','running')"
        )
        for experiment in unfinished:
            db.execute(
                """UPDATE experiments SET status='interrupted',finished_at=?,
                error=COALESCE(error,'service restarted before completion') WHERE id=?""",
                (utcnow(), experiment["id"]),
            )
            db.execute(
                """INSERT INTO audit_events(
                created_at,action,entity_type,entity_id,owner_user_id,details_json)
                VALUES(?,?,?,?,?,?)""",
                (
                    utcnow(),
                    "experiment.interrupted",
                    "experiment",
                    str(experiment["id"]),
                    experiment["owner_user_id"],
                    json.dumps({"previous_status": experiment["status"]}),
                ),
            )
    # The track column did not exist in v0.2. Infer only the unambiguous legacy
    # repository-agent runs; keep config_hash NULL so the UI still identifies
    # them as non-frozen historical evidence.
    for legacy in db.rows("SELECT * FROM experiments WHERE config_hash IS NULL"):
        provider_ids = json.loads(legacy["provider_ids_json"])
        benchmark_ids = json.loads(legacy["benchmark_ids_json"])
        if not provider_ids or not benchmark_ids:
            continue
        provider_marks = ",".join("?" for _ in provider_ids)
        benchmark_marks = ",".join("?" for _ in benchmark_ids)
        providers_are_agents = db.row(
            f"""SELECT COUNT(*) n FROM providers WHERE id IN ({provider_marks})
            AND kind IN ('claude_code_agent','openclaw_agent')""",
            tuple(provider_ids),
        )["n"] == len(set(provider_ids))
        benchmarks_are_repositories = db.row(
            f"""SELECT COUNT(*) n FROM benchmarks WHERE id IN ({benchmark_marks})
            AND task_type='repository_agent'""",
            tuple(benchmark_ids),
        )["n"] == len(set(benchmark_ids))
        if providers_are_agents and benchmarks_are_repositories:
            db.execute(
                "UPDATE experiments SET track='native_agent' WHERE id=?",
                (legacy["id"],),
            )
    manager = EvaluationManager(db, secrets, root)
    health_monitor = ProviderHealthMonitor(db, secrets, root)
    app = FastAPI(title="Prism Model × Agent Eval", version="0.9.0")
    app.state.db, app.state.secrets, app.state.manager = db, secrets, manager
    app.state.health_monitor = health_monitor

    @app.on_event("startup")
    def start_health_monitor() -> None:
        recover_unfinished_experiments()
        health_monitor.start()

    @app.on_event("shutdown")
    def stop_health_monitor() -> None:
        health_monitor.stop()

    @app.middleware("http")
    async def authentication(request: Request, call_next: Any) -> Response:
        if trusted_local:
            # The parent Agent Eval application is intentionally local and
            # login-free. Reuse Prism's authorization-aware handlers with the
            # bootstrap administrator as the trusted local principal.
            request.state.user = bootstrap_admin
            return await call_next(request)
        public_paths = {"/api/health", "/api/auth/login", "/api/auth/bootstrap-status"}
        if request.url.path.startswith("/api/") and request.url.path not in public_paths:
            user = session_user(db, request.cookies.get("prism_session"))
            if not user:
                return JSONResponse({"detail": "authentication required"}, status_code=401)
            request.state.user = user
        return await call_next(request)

    def current_user(request: Request) -> dict[str, Any]:
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(401, "authentication required")
        return user

    def require_write(request: Request) -> dict[str, Any]:
        user = current_user(request)
        if user["role"] not in {"admin", "evaluator"}:
            raise HTTPException(403, "write permission required")
        return user

    def require_admin(request: Request) -> dict[str, Any]:
        user = current_user(request)
        if user["role"] != "admin":
            raise HTTPException(403, "administrator permission required")
        return user

    def can_access(user: dict[str, Any], owner_user_id: int | None) -> bool:
        return user["role"] == "admin" or owner_user_id == user["id"]

    def can_access_benchmark(
        user: dict[str, Any], benchmark: dict[str, Any], write: bool = False
    ) -> bool:
        if user["role"] == "admin":
            return True
        if benchmark.get("owner_user_id") == user["id"]:
            return not write or user["role"] == "evaluator"
        if write:
            return False
        return bool(benchmark.get("official")) or benchmark.get("visibility") in {
            "shared",
            "public",
        }

    def require_benchmark(
        request: Request, benchmark_id: str, write: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        user = current_user(request)
        benchmark = db.row("SELECT * FROM benchmarks WHERE id=?", (benchmark_id,))
        if not benchmark or not can_access_benchmark(user, benchmark, write):
            raise HTTPException(404, "benchmark not found")
        return user, benchmark

    def audit(
        action: str,
        entity_type: str,
        entity_id: str | int | None,
        details: dict[str, Any] | None = None,
        owner_user_id: int | None = None,
    ) -> None:
        db.execute(
            """INSERT INTO audit_events(
            created_at,action,entity_type,entity_id,owner_user_id,details_json)
            VALUES(?,?,?,?,?,?)""",
            (
                utcnow(),
                action,
                entity_type,
                str(entity_id) if entity_id is not None else None,
                owner_user_id,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )

    @app.get("/api/auth/bootstrap-status")
    def bootstrap_status() -> dict[str, Any]:
        return {
            "configured": True,
            "credential_file_exists": (data / "bootstrap-admin.txt").is_file(),
        }

    @app.post("/api/auth/login")
    def login(body: LoginIn) -> Response:
        user = db.row("SELECT * FROM users WHERE username=?", (body.username,))
        if not user or not user["active"] or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "invalid username or password")
        token = create_session(db, user["id"])
        db.execute(
            "UPDATE users SET last_login_at=?,updated_at=? WHERE id=?",
            (utcnow(), utcnow(), user["id"]),
        )
        user = db.row("SELECT * FROM users WHERE id=?", (user["id"],))
        response = JSONResponse(public_user(user))
        response.set_cookie(
            "prism_session",
            token,
            httponly=True,
            samesite="strict",
            secure=os.environ.get("PRISM_SECURE_COOKIES", "0") == "1",
            max_age=12 * 3600,
        )
        audit("auth.login", "user", user["id"], owner_user_id=user["id"])
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request) -> Response:
        user = current_user(request)
        revoke_session(db, request.cookies.get("prism_session"))
        response = JSONResponse({"ok": True})
        response.delete_cookie("prism_session")
        audit("auth.logout", "user", user["id"], owner_user_id=user["id"])
        return response

    @app.get("/api/auth/me")
    def me(request: Request) -> dict[str, Any]:
        return public_user(current_user(request))

    @app.get("/api/users")
    def users(request: Request) -> list[dict[str, Any]]:
        require_admin(request)
        return [public_user(row) for row in db.rows("SELECT * FROM users ORDER BY id")]

    @app.post("/api/users")
    def create_user(request: Request, body: UserIn) -> dict[str, Any]:
        admin = require_admin(request)
        if body.role not in {"admin", "evaluator", "viewer"}:
            raise HTTPException(400, "unsupported role")
        if db.row("SELECT id FROM users WHERE username=?", (body.username,)):
            raise HTTPException(409, "username already exists")
        now = utcnow()
        user_id = db.execute(
            """INSERT INTO users(
            username,display_name,password_hash,role,active,created_at,updated_at)
            VALUES(?,?,?,?,1,?,?)""",
            (body.username, body.display_name, hash_password(body.password), body.role, now, now),
        )
        audit(
            "user.created",
            "user",
            user_id,
            {"username": body.username, "role": body.role, "actor": admin["id"]},
            owner_user_id=admin["id"],
        )
        return public_user(db.row("SELECT * FROM users WHERE id=?", (user_id,)))

    @app.put("/api/users/{user_id}")
    def update_user(request: Request, user_id: int, body: UserUpdateIn) -> dict[str, Any]:
        admin = require_admin(request)
        target = db.row("SELECT * FROM users WHERE id=?", (user_id,))
        if not target:
            raise HTTPException(404, "user not found")
        if body.role not in {"admin", "evaluator", "viewer"}:
            raise HTTPException(400, "unsupported role")
        if target["id"] == admin["id"] and (not body.active or body.role != "admin"):
            raise HTTPException(400, "you cannot remove your own active administrator access")
        password_hash = hash_password(body.password) if body.password else target["password_hash"]
        db.execute(
            """UPDATE users SET display_name=?,role=?,active=?,password_hash=?,updated_at=?
            WHERE id=?""",
            (
                body.display_name,
                body.role,
                int(body.active),
                password_hash,
                utcnow(),
                user_id,
            ),
        )
        if body.password or not body.active:
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        audit(
            "user.updated",
            "user",
            user_id,
            {"role": body.role, "active": body.active, "actor": admin["id"]},
            owner_user_id=admin["id"],
        )
        return public_user(db.row("SELECT * FROM users WHERE id=?", (user_id,)))

    @app.get("/api/settings")
    def get_settings(request: Request) -> dict[str, Any]:
        user = current_user(request)
        row = db.row("SELECT * FROM user_settings WHERE user_id=?", (user["id"],))
        if not row:
            db.execute(
                """INSERT INTO user_settings(user_id,updated_at)
                VALUES(?,?)""",
                (user["id"], utcnow()),
            )
            row = db.row("SELECT * FROM user_settings WHERE user_id=?", (user["id"],))
        return row

    @app.put("/api/settings")
    def update_settings(request: Request, body: UserSettingsIn) -> dict[str, Any]:
        user = current_user(request)
        if body.theme not in {"forest", "ocean", "indigo", "dark"}:
            raise HTTPException(400, "unsupported theme")
        if body.language not in {"zh-CN", "en"}:
            raise HTTPException(400, "unsupported language")
        db.execute(
            """INSERT INTO user_settings(
            user_id,health_check_enabled,health_check_interval_minutes,theme,language,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            health_check_enabled=excluded.health_check_enabled,
            health_check_interval_minutes=excluded.health_check_interval_minutes,
            theme=excluded.theme,language=excluded.language,updated_at=excluded.updated_at""",
            (
                user["id"],
                int(body.health_check_enabled),
                body.health_check_interval_minutes,
                body.theme,
                body.language,
                utcnow(),
            ),
        )
        audit(
            "settings.updated",
            "user",
            user["id"],
            {
                "health_check_enabled": body.health_check_enabled,
                "health_check_interval_minutes": body.health_check_interval_minutes,
                "theme": body.theme,
                "language": body.language,
            },
            owner_user_id=user["id"],
        )
        return get_settings(request)

    @app.get("/api/provider-health")
    def provider_health_history(
        request: Request, limit: int = Query(default=100, ge=1, le=1000)
    ) -> list[dict[str, Any]]:
        user = current_user(request)
        if user["role"] == "admin":
            return db.rows(
                """SELECT h.*,p.name provider_name,p.model FROM provider_health_checks h
                JOIN providers p ON p.id=h.provider_id ORDER BY h.id DESC LIMIT ?""",
                (limit,),
            )
        return db.rows(
            """SELECT h.*,p.name provider_name,p.model FROM provider_health_checks h
            JOIN providers p ON p.id=h.provider_id WHERE h.owner_user_id=?
            ORDER BY h.id DESC LIMIT ?""",
            (user["id"], limit),
        )

    @app.get("/api/admin/usage")
    def admin_usage(request: Request) -> dict[str, Any]:
        require_admin(request)
        users = db.rows(
            """SELECT u.id,u.username,u.display_name,u.role,u.active,u.auth_source,
            u.last_login_at,u.created_at,
            (SELECT COUNT(*) FROM providers p WHERE p.owner_user_id=u.id) providers,
            (SELECT COUNT(*) FROM benchmarks b WHERE b.owner_user_id=u.id) benchmarks,
            (SELECT COUNT(*) FROM experiments e WHERE e.owner_user_id=u.id) experiments,
            (SELECT COUNT(*) FROM results r JOIN experiments e ON e.id=r.experiment_id
             WHERE e.owner_user_id=u.id) results,
            (SELECT COALESCE(SUM(COALESCE(r.input_tokens,0)+COALESCE(r.output_tokens,0)),0)
             FROM results r JOIN experiments e ON e.id=r.experiment_id
             WHERE e.owner_user_id=u.id) tokens,
            (SELECT COALESCE(SUM(COALESCE(r.cost_usd,0)),0)
             FROM results r JOIN experiments e ON e.id=r.experiment_id
             WHERE e.owner_user_id=u.id) cost_usd
            FROM users u ORDER BY u.id"""
        )
        return {
            "users": users,
            "totals": {
                "users": len(users),
                "active_users": sum(bool(row["active"]) for row in users),
                "experiments": sum(row["experiments"] for row in users),
                "results": sum(row["results"] for row in users),
                "tokens": sum(row["tokens"] for row in users),
                "cost_usd": sum(float(row["cost_usd"] or 0) for row in users),
            },
        }

    def public_provider(row: dict[str, Any]) -> dict[str, Any]:
        row = decode_json_fields(row, "settings_json")
        row["has_api_key"] = bool(row.pop("api_key_cipher", None))
        latest_health = db.row(
            """SELECT ok,checked_at FROM provider_health_checks
            WHERE provider_id=? ORDER BY id DESC LIMIT 1""",
            (row["id"],),
        )
        row["availability"] = (
            "available"
            if latest_health and latest_health.get("ok") == 1
            else "unavailable"
            if latest_health and latest_health.get("ok") == 0
            else "pending"
        )
        row["availability_checked_at"] = (
            latest_health.get("checked_at") if latest_health else None
        )
        return row

    def result_rows(experiment_id: int, limit: int) -> list[dict[str, Any]]:
        rows = db.rows(
            """SELECT r.*,p.name provider_name,p.model,b.id benchmark_id,b.name benchmark_name,
            bi.item_key,bi.category,bi.prompt,bi.expected_json,bi.scorer_type
            FROM results r JOIN providers p ON p.id=r.provider_id
            JOIN benchmark_items bi ON bi.id=r.benchmark_item_id
            JOIN benchmarks b ON b.id=bi.benchmark_id WHERE r.experiment_id=?
            ORDER BY r.id DESC LIMIT ?""",
            (experiment_id, limit),
        )
        for row in rows:
            try:
                row["expected"] = json.loads(row.pop("expected_json") or "null")
            except json.JSONDecodeError:
                row["expected"] = row.pop("expected_json", None)
        return rows

    def display_experiment_name(row: dict[str, Any]) -> dict[str, Any]:
        """Replace irreversibly corrupted legacy names without mutating audit data."""
        name = str(row.get("name") or "")
        if "\ufffd" not in name and name.count("?") < 2:
            return row
        track_name = {
            "model_direct": "模型直测",
            "reference_agent": "统一执行器 Agent 评测",
            "native_agent": "原生 Agent 评测",
        }.get(row.get("track"), "模型与 Agent 评测")
        try:
            benchmark_ids = json.loads(row.get("benchmark_ids_json") or "[]")
        except json.JSONDecodeError:
            benchmark_ids = []
        benchmark_names = []
        for benchmark_id in benchmark_ids[:3]:
            benchmark = db.row("SELECT name FROM benchmarks WHERE id=?", (benchmark_id,))
            benchmark_names.append(benchmark["name"] if benchmark else str(benchmark_id))
        suffix = "、".join(benchmark_names)
        if len(benchmark_ids) > 3:
            suffix += f" 等 {len(benchmark_ids)} 个题库"
        row["original_name"] = name
        row["name"] = f"{track_name} · {suffix}" if suffix else track_name
        row["name_repaired"] = True
        return row

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": app.version,
            "claude": bool(shutil.which("claude")),
            "openclaw": bool(shutil.which("openclaw")),
            "codex": bool(resolve_executable("codex")),
        }

    @app.get("/api/providers")
    def providers(request: Request) -> list[dict[str, Any]]:
        user = current_user(request)
        sql = "SELECT * FROM providers ORDER BY id DESC"
        args: tuple[Any, ...] = ()
        if user["role"] != "admin":
            sql = "SELECT * FROM providers WHERE owner_user_id=? OR shared=1 ORDER BY id DESC"
            args = (user["id"],)
        return [
            public_provider(row)
            for row in db.rows(sql, args)
        ]

    @app.post("/api/providers")
    def create_provider(request: Request, body: ProviderIn) -> dict[str, Any]:
        user = require_write(request)
        if body.kind not in KIND_TO_ADAPTER:
            raise HTTPException(400, "unsupported provider kind")
        if body.kind == "custom_cli_agent" and user["role"] != "admin":
            raise HTTPException(403, "only administrators can create custom CLI agents")
        now = utcnow()
        provider_id = db.execute(
            """INSERT INTO providers(
            name,kind,model,base_url,api_key_cipher,settings_json,owner_user_id,shared,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                body.name,
                body.kind,
                body.model,
                body.base_url,
                secrets.encrypt(body.api_key),
                json.dumps(body.settings),
                user["id"],
                int(body.shared or body.kind == "custom_cli_agent"),
                now,
                now,
            ),
        )
        audit(
            "provider.created",
            "provider",
            provider_id,
            {"name": body.name, "kind": body.kind, "model": body.model},
            owner_user_id=user["id"],
        )
        return public_provider(db.row("SELECT * FROM providers WHERE id=?", (provider_id,)))

    @app.post("/api/providers/auto")
    def ensure_auto_provider(request: Request, body: AutoProviderIn) -> dict[str, Any]:
        """Create or reuse a server-configured LiteLLM provider for unified UI runs."""
        user = require_write(request)
        runtime_agent = "codex" if body.agent == "codex" else None
        try:
            resolved = resolve_model_profile(
                root,
                profile_name=body.profile,
                model_override=body.model,
                agent=runtime_agent,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not resolved.api_base or not resolved.environment.get("LITELLM_API_KEY"):
            raise HTTPException(400, "Question-bank evaluation requires a LiteLLM profile")
        kind = (
            "openai"
            if body.agent == "direct"
            else ("codex_cli_agent" if body.task_kind == "repo" else "codex_cli_direct")
        )
        provider_model = resolved.model_for_agent("codex") if body.agent == "codex" else resolved.model
        openai_base = resolved.environment["OPENAI_BASE_URL"]
        name = f"Unified · {body.agent} · {resolved.model}"
        existing = db.row(
            """SELECT * FROM providers WHERE owner_user_id=? AND kind=? AND model=?
            AND COALESCE(base_url,'')=? ORDER BY id DESC LIMIT 1""",
            (user["id"], kind, provider_model, openai_base),
        )
        if existing:
            return public_provider(existing)
        now = utcnow()
        provider_id = db.execute(
            """INSERT INTO providers(
            name,kind,model,base_url,api_key_cipher,settings_json,owner_user_id,shared,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                name,
                kind,
                provider_model,
                openai_base,
                secrets.encrypt(resolved.environment["LITELLM_API_KEY"]),
                json.dumps(
                    {
                        "extra_args": list(resolved.agent_args),
                        "timeout_seconds": 300,
                        "max_tokens": 4096,
                        "auto_managed": True,
                        "requested_model": resolved.model,
                        "profile": resolved.name,
                    }
                ),
                user["id"],
                0,
                now,
                now,
            ),
        )
        return public_provider(db.row("SELECT * FROM providers WHERE id=?", (provider_id,)))

    @app.put("/api/providers/{provider_id}")
    def update_provider(request: Request, provider_id: int, body: ProviderIn) -> dict[str, Any]:
        user = require_write(request)
        old = db.row("SELECT * FROM providers WHERE id=?", (provider_id,))
        if not old:
            raise HTTPException(404, "provider not found")
        if not can_access(user, old["owner_user_id"]):
            raise HTTPException(404, "provider not found")
        if body.kind not in KIND_TO_ADAPTER:
            raise HTTPException(400, "unsupported provider kind")
        if body.kind == "custom_cli_agent" and user["role"] != "admin":
            raise HTTPException(403, "only administrators can configure custom CLI agents")
        cipher = secrets.encrypt(body.api_key) if body.api_key else old["api_key_cipher"]
        db.execute(
            """UPDATE providers SET name=?,kind=?,model=?,base_url=?,api_key_cipher=?,
            settings_json=?,shared=?,updated_at=? WHERE id=?""",
            (
                body.name,
                body.kind,
                body.model,
                body.base_url,
                cipher,
                json.dumps(body.settings),
                int(body.shared or body.kind == "custom_cli_agent"),
                utcnow(),
                provider_id,
            ),
        )
        audit(
            "provider.updated",
            "provider",
            provider_id,
            {"name": body.name, "kind": body.kind, "model": body.model},
            owner_user_id=old["owner_user_id"],
        )
        return public_provider(db.row("SELECT * FROM providers WHERE id=?", (provider_id,)))

    @app.delete("/api/providers/{provider_id}")
    def delete_provider(request: Request, provider_id: int) -> dict[str, bool]:
        user = require_write(request)
        provider = db.row("SELECT id,name FROM providers WHERE id=?", (provider_id,))
        if not provider:
            raise HTTPException(404, "provider not found")
        full_provider = db.row("SELECT * FROM providers WHERE id=?", (provider_id,))
        if not can_access(user, full_provider["owner_user_id"]):
            raise HTTPException(404, "provider not found")
        used = db.row("SELECT COUNT(*) n FROM results WHERE provider_id=?", (provider_id,))
        if used and used["n"]:
            raise HTTPException(409, "provider has historical results and cannot be deleted")
        db.execute("DELETE FROM providers WHERE id=?", (provider_id,))
        audit("provider.deleted", "provider", provider_id, {"name": provider["name"]}, full_provider["owner_user_id"])
        return {"ok": True}

    @app.post("/api/providers/{provider_id}/test")
    def test_provider(request: Request, provider_id: int) -> dict[str, Any]:
        user = require_write(request)
        row = db.row("SELECT * FROM providers WHERE id=?", (provider_id,))
        if not row:
            raise HTTPException(404, "provider not found")
        if not can_access(user, row["owner_user_id"]) and not row["shared"]:
            raise HTTPException(404, "provider not found")
        result = test_provider_connection(row, secrets, root)
        health_monitor.record(row, result, "manual")
        audit(
            "provider.tested",
            "provider",
            provider_id,
            {"ok": result.get("ok"), "actual_model": result.get("actual_model")},
            owner_user_id=row["owner_user_id"],
        )
        return result

    @app.get("/api/benchmarks")
    def benchmarks(request: Request) -> list[dict[str, Any]]:
        user = current_user(request)
        visible: list[dict[str, Any]] = []
        for raw in db.rows("SELECT * FROM benchmarks ORDER BY official DESC,id"):
            if not can_access_benchmark(user, raw):
                continue
            row = decode_json_fields(raw, "metadata_json")
            if (
                user["role"] != "admin"
                and not row.get("official")
                and row.get("owner_user_id") != user["id"]
            ):
                row["item_count"] = db.row(
                    "SELECT COUNT(*) n FROM benchmark_items WHERE benchmark_id=? AND access_level='shared'",
                    (row["id"],),
                )["n"]
            visible.append(row)
        return visible

    @app.get("/api/benchmarks/{benchmark_id}")
    def benchmark_detail(request: Request, benchmark_id: str) -> dict[str, Any]:
        user, benchmark = require_benchmark(request, benchmark_id)
        benchmark = decode_json_fields(benchmark, "metadata_json")
        shared_only = (
            user["role"] != "admin"
            and not benchmark.get("official")
            and benchmark.get("owner_user_id") != user["id"]
        )
        access_filter = " AND access_level='shared'" if shared_only else ""
        benchmark["categories"] = db.rows(
            """SELECT COALESCE(category,'uncategorized') category,COUNT(*) item_count
            FROM benchmark_items WHERE benchmark_id=?"""
            + access_filter
            + " GROUP BY category ORDER BY item_count DESC",
            (benchmark_id,),
        )
        if shared_only:
            benchmark["item_count"] = sum(
                row["item_count"] for row in benchmark["categories"]
            )
        benchmark["versions"] = [
            decode_json_fields(row, "metadata_json")
            for row in db.rows(
                "SELECT * FROM benchmark_versions WHERE benchmark_id=? ORDER BY id DESC",
                (benchmark_id,),
            )
        ]
        benchmark["can_manage"] = user["role"] == "admin" or (
            user["role"] == "evaluator"
            and benchmark.get("owner_user_id") == user["id"]
        )
        return benchmark

    @app.post("/api/benchmarks/{benchmark_id}/install")
    def install(
        request: Request, benchmark_id: str, limit: int | None = Query(default=None, ge=1)
    ) -> dict[str, Any]:
        user = require_write(request)
        if os.environ.get("PRISM_ALLOW_ONLINE_BENCHMARK_INSTALL", "0") != "1":
            raise HTTPException(
                409,
                "online benchmark installation is disabled; upload a JSON or JSONL pack",
            )
        try:
            count = install_benchmark(db, benchmark_id, limit)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"download/import failed: {exc}") from exc
        audit(
            "benchmark.installed",
            "benchmark",
            benchmark_id,
            {"item_count": count},
            owner_user_id=user["id"],
        )
        return benchmark_detail(request, benchmark_id) | {"ok": True, "item_count": count}

    @app.post("/api/benchmarks/import")
    def import_benchmark(request: Request, body: BenchmarkImportIn) -> dict[str, Any]:
        user = require_write(request)
        storage_id = f"u{user['id']}--{body.id}"
        if body.visibility not in {"private", "shared"}:
            raise HTTPException(400, "visibility must be private or shared")
        invalid_levels = sorted(
            {item.access_level for item in body.items} - {"private", "shared"}
        )
        if invalid_levels:
            raise HTTPException(400, "item access_level must be private or shared")
        allowed_scorers = {
            "exact",
            "contains",
            "numeric_answer",
            "regex",
            "json",
            "python_expression",
            "multiple_choice",
        }
        unsupported = sorted({item.scorer for item in body.items} - allowed_scorers)
        if unsupported:
            raise HTTPException(400, f"unsupported custom scorers: {', '.join(unsupported)}")
        if len({item.key for item in body.items}) != len(body.items):
            raise HTTPException(400, "custom benchmark item keys must be unique")
        existing = db.row("SELECT * FROM benchmarks WHERE id=?", (storage_id,))
        if existing and existing["official"]:
            raise HTTPException(409, "official benchmark IDs cannot be replaced")
        if existing and not body.replace:
            raise HTTPException(409, "benchmark already exists; set replace=true to replace it")
        if existing and not can_access_benchmark(user, existing, write=True):
            raise HTTPException(404, "benchmark not found")
        if existing:
            used = db.row(
                """SELECT COUNT(*) n FROM results r JOIN benchmark_items bi
                ON bi.id=r.benchmark_item_id WHERE bi.benchmark_id=?""",
                (storage_id,),
            )["n"]
            if used:
                raise HTTPException(409, "benchmark has historical results and cannot be replaced")
        item_payload = [item.model_dump() for item in body.items]
        digest = hashlib.sha256(
            json.dumps(item_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        now = utcnow()
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO benchmarks(
                id,name,description,source_url,license,status,item_count,metadata_json,installed_at,
                version,source_revision,content_sha256,task_type,language,official,
                prompt_template_version,scorer_version,owner_user_id,visibility,slug)
                VALUES(?,?,?,?,?,'installed',?,?,?,?,'user-import',?,?,?,?, '1','custom-v1',?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description,
                source_url=excluded.source_url,license=excluded.license,status='installed',
                item_count=excluded.item_count,metadata_json=excluded.metadata_json,
                installed_at=excluded.installed_at,version=excluded.version,
                source_revision=excluded.source_revision,content_sha256=excluded.content_sha256,
                task_type=excluded.task_type,language=excluded.language,
                owner_user_id=excluded.owner_user_id,visibility=excluded.visibility""",
                (
                    storage_id,
                    body.name,
                    body.description,
                    body.source_url,
                    body.license,
                    len(body.items),
                    json.dumps({"user_imported": True}),
                    now,
                    body.version,
                    digest,
                    body.task_type,
                    body.language,
                    0,
                    user["id"],
                    body.visibility,
                    body.id,
                ),
            )
            conn.execute("DELETE FROM benchmark_items WHERE benchmark_id=?", (storage_id,))
            conn.executemany(
                """INSERT INTO benchmark_items(
                benchmark_id,item_key,category,prompt,expected_json,scorer_type,metadata_json,access_level)
                VALUES(?,?,?,?,?,?,?,?)""",
                [
                    (
                        storage_id,
                        item.key,
                        item.category,
                        item.prompt,
                        json.dumps(item.expected, ensure_ascii=False),
                        item.scorer,
                        json.dumps(item.metadata, ensure_ascii=False),
                        item.access_level,
                    )
                    for item in body.items
                ],
            )
            conn.execute(
                """INSERT OR IGNORE INTO benchmark_versions(
                benchmark_id,version,source_revision,content_sha256,item_count,importer_version,
                installed_at,metadata_json) VALUES(?,?,?,?,?,'user-import-v1',?,?)""",
                (
                    storage_id,
                    body.version,
                    "user-import",
                    digest,
                    len(body.items),
                    now,
                    json.dumps({"user_imported": True}),
                ),
            )
        audit(
            "benchmark.imported",
            "benchmark",
            storage_id,
            {"version": body.version, "item_count": len(body.items), "sha256": digest},
            owner_user_id=user["id"],
        )
        return benchmark_detail(request, storage_id)

    @app.get("/api/benchmarks/{benchmark_id}/items")
    def benchmark_items(
        request: Request, benchmark_id: str, limit: int = Query(default=20, le=1000)
    ) -> list[dict[str, Any]]:
        user, benchmark = require_benchmark(request, benchmark_id)
        private_access = (
            user["role"] == "admin"
            or bool(benchmark.get("official"))
            or benchmark.get("owner_user_id") == user["id"]
        )
        access_filter = "" if private_access else " AND access_level='shared'"
        return db.rows(
            """SELECT id,item_key,category,prompt,scorer_type,access_level FROM benchmark_items
            WHERE benchmark_id=?""" + access_filter + " ORDER BY id LIMIT ?",
            (benchmark_id, limit),
        )

    @app.post("/api/benchmarks/import-jsonl")
    async def import_benchmark_jsonl(request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            lines = [
                json.loads(line)
                for line in (await request.body()).decode("utf-8-sig").splitlines()
                if line.strip()
            ]
            if len(lines) < 2 or lines[0].get("_type") != "benchmark":
                raise ValueError("first JSONL line must be a benchmark manifest")
            manifest = dict(lines[0])
            manifest.pop("_type", None)
            manifest["items"] = lines[1:]
            body = BenchmarkImportIn.model_validate(manifest)
        except Exception as exc:
            raise HTTPException(400, f"invalid JSONL benchmark pack: {exc}") from exc
        return import_benchmark(request, body)

    @app.get("/api/benchmarks/{benchmark_id}/export")
    def export_benchmark(
        request: Request,
        benchmark_id: str,
        format: str = Query(default="json", pattern="^(json|jsonl)$"),
    ) -> Response:
        user, benchmark = require_benchmark(request, benchmark_id)
        private_access = (
            user["role"] == "admin"
            or bool(benchmark.get("official"))
            or benchmark.get("owner_user_id") == user["id"]
        )
        access_filter = "" if private_access else " AND access_level='shared'"
        rows = db.rows(
            "SELECT * FROM benchmark_items WHERE benchmark_id=?"
            + access_filter
            + " ORDER BY id",
            (benchmark_id,),
        )
        items = [
            {
                "key": row["item_key"],
                "category": row["category"] or "custom",
                "prompt": row["prompt"],
                "expected": json.loads(row["expected_json"])
                if row["expected_json"]
                else None,
                "scorer": row["scorer_type"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
                "access_level": row.get("access_level") or "private",
            }
            for row in rows
        ]
        manifest = {
            "id": benchmark.get("slug") or benchmark["id"],
            "name": benchmark["name"],
            "description": benchmark["description"],
            "source_url": benchmark.get("source_url"),
            "license": benchmark.get("license") or "Internal",
            "version": benchmark["version"],
            "task_type": benchmark["task_type"],
            "language": benchmark["language"],
            "visibility": benchmark.get("visibility") or "private",
            "replace": False,
        }
        if format == "json":
            content = json.dumps(manifest | {"items": items}, ensure_ascii=False, indent=2)
            media_type = "application/json"
        else:
            content = "\n".join(
                [json.dumps({"_type": "benchmark"} | manifest, ensure_ascii=False)]
                + [json.dumps(item, ensure_ascii=False) for item in items]
            ) + "\n"
            media_type = "application/x-ndjson"
        audit(
            "benchmark.exported",
            "benchmark",
            benchmark_id,
            {"format": format, "item_count": len(items)},
            owner_user_id=benchmark.get("owner_user_id") or user["id"],
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{benchmark_id}.{format}"'
            },
        )

    @app.get("/api/benchmarks/{benchmark_id}/items/{item_id}/export")
    def export_benchmark_item(
        request: Request, benchmark_id: str, item_id: int
    ) -> Response:
        user, benchmark = require_benchmark(request, benchmark_id)
        row = db.row(
            "SELECT * FROM benchmark_items WHERE benchmark_id=? AND id=?",
            (benchmark_id, item_id),
        )
        if not row:
            raise HTTPException(404, "benchmark item not found")
        if (
            not benchmark.get("official")
            and benchmark.get("owner_user_id") != user["id"]
            and user["role"] != "admin"
            and row.get("access_level") != "shared"
        ):
            raise HTTPException(404, "benchmark item not found")
        payload = {
            "key": row["item_key"],
            "category": row["category"],
            "prompt": row["prompt"],
            "expected": json.loads(row["expected_json"])
            if row["expected_json"]
            else None,
            "scorer": row["scorer_type"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "access_level": row.get("access_level") or "private",
        }
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{benchmark_id}-{row["item_key"]}.json"'
            },
        )

    @app.post("/api/benchmarks/{benchmark_id}/items/{item_id}/try")
    def try_benchmark_item(
        request: Request, benchmark_id: str, item_id: int, body: ItemTryIn
    ) -> dict[str, Any]:
        user = require_write(request)
        _, benchmark = require_benchmark(request, benchmark_id)
        item = db.row(
            "SELECT * FROM benchmark_items WHERE benchmark_id=? AND id=?",
            (benchmark_id, item_id),
        )
        if not item:
            raise HTTPException(404, "benchmark item not found")
        if (
            not benchmark.get("official")
            and benchmark.get("owner_user_id") != user["id"]
            and user["role"] != "admin"
            and item.get("access_level") != "shared"
        ):
            raise HTTPException(404, "benchmark item not found")
        provider = db.row("SELECT * FROM providers WHERE id=?", (body.provider_id,))
        if not provider or (
            not can_access(user, provider.get("owner_user_id")) and not provider.get("shared")
        ):
            raise HTTPException(404, "provider not found")
        latest_health = db.row(
            """SELECT ok FROM provider_health_checks
            WHERE provider_id=? ORDER BY id DESC LIMIT 1""",
            (body.provider_id,),
        )
        if not latest_health or latest_health.get("ok") != 1:
            raise HTTPException(409, "仅可选择模型列表中状态为“可用”的模型或 Agent")
        try:
            result = manager.preview(provider, item, body.allow_unsafe_code)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        audit(
            "benchmark.item_tried",
            "benchmark_item",
            item_id,
            {
                "benchmark_id": benchmark_id,
                "provider_id": body.provider_id,
                "passed": result["passed"],
            },
            owner_user_id=user["id"],
        )
        return result

    @app.put("/api/benchmarks/{benchmark_id}/items/{item_id}/access")
    def update_benchmark_item_access(
        request: Request, benchmark_id: str, item_id: int, body: ItemAccessIn
    ) -> dict[str, Any]:
        user = require_write(request)
        _, benchmark = require_benchmark(request, benchmark_id, write=True)
        if benchmark.get("official"):
            raise HTTPException(400, "default benchmark item permissions are read-only")
        if body.access_level not in {"private", "shared"}:
            raise HTTPException(400, "access_level must be private or shared")
        item = db.row(
            "SELECT * FROM benchmark_items WHERE benchmark_id=? AND id=?",
            (benchmark_id, item_id),
        )
        if not item:
            raise HTTPException(404, "benchmark item not found")
        db.execute(
            "UPDATE benchmark_items SET access_level=? WHERE id=?",
            (body.access_level, item_id),
        )
        audit(
            "benchmark.item_access_updated",
            "benchmark_item",
            item_id,
            {"benchmark_id": benchmark_id, "access_level": body.access_level},
            owner_user_id=benchmark.get("owner_user_id") or user["id"],
        )
        return {"ok": True, "access_level": body.access_level}

    @app.get("/api/protocols")
    def protocols() -> dict[str, Any]:
        return {
            "tracks": TRACKS,
            "sampling_strategies": ["stratified", "random", "ordered"],
            "default_budget": {
                "timeout_seconds_per_task": 300,
                "max_output_tokens": 4096,
                "max_context_chars": 60000,
                "max_files_changed": 8,
            },
            "reference_agent": {
                "protocol_version": PROTOCOL_VERSION,
                "provider_kinds": ["anthropic", "openai"],
                "task_type": "repository_agent",
                "tool_calls": 0,
            },
        }

    @app.get("/api/suites")
    def suites() -> list[dict[str, Any]]:
        installed = {
            row["id"]
            for row in db.rows("SELECT id FROM benchmarks WHERE item_count>0")
        }
        return [
            suite
            | {
                "installed_benchmark_ids": [
                    benchmark_id
                    for benchmark_id in suite["benchmark_ids"]
                    if benchmark_id in installed
                ],
                "missing_benchmark_ids": [
                    benchmark_id
                    for benchmark_id in suite["benchmark_ids"]
                    if benchmark_id not in installed
                ],
            }
            for suite in SUITES
        ]

    @app.post("/api/experiments")
    def create_experiment(request: Request, body: ExperimentIn) -> dict[str, Any]:
        user = require_write(request)
        if body.track not in TRACKS:
            raise HTTPException(400, "unsupported evaluation track")
        if body.sampling_strategy not in {"ordered", "random", "stratified"}:
            raise HTTPException(400, "unsupported sampling strategy")
        placeholders = ",".join("?" for _ in body.provider_ids)
        provider_rows = db.rows(
            f"SELECT * FROM providers WHERE id IN ({placeholders})",
            tuple(body.provider_ids),
        )
        if len(provider_rows) != len(set(body.provider_ids)):
            raise HTTPException(400, "one or more providers do not exist")
        if user["role"] != "admin" and any(
            row["owner_user_id"] != user["id"] and not row["shared"] for row in provider_rows
        ):
            raise HTTPException(400, "one or more providers do not exist")
        agent_kinds = {
            "claude_code_agent",
            "openclaw_agent",
            "codex_agent",
            "custom_cli_agent",
            "custom_http_agent",
        }
        if body.track == "model_direct" and any(
            row["kind"] in agent_kinds for row in provider_rows
        ):
            raise HTTPException(400, "model_direct track only accepts direct providers")
        if body.track == "native_agent" and any(
            row["kind"] not in agent_kinds for row in provider_rows
        ):
            raise HTTPException(400, "native_agent track only accepts agent providers")
        if body.track == "reference_agent" and any(
            row["kind"] not in {"anthropic", "openai"} for row in provider_rows
        ):
            raise HTTPException(400, "reference_agent track only accepts HTTP API models")

        budget = {
            "timeout_seconds_per_task": int(body.budget.get("timeout_seconds_per_task", 300)),
            "max_output_tokens": int(body.budget.get("max_output_tokens", 4096)),
            "max_context_chars": int(body.budget.get("max_context_chars", 60000)),
            "max_files_changed": int(body.budget.get("max_files_changed", 8)),
        }
        if not 10 <= budget["timeout_seconds_per_task"] <= 3600:
            raise HTTPException(400, "timeout budget must be between 10 and 3600 seconds")
        if not 32 <= budget["max_output_tokens"] <= 131072:
            raise HTTPException(400, "output token budget must be between 32 and 131072")
        if not 1000 <= budget["max_context_chars"] <= 1_000_000:
            raise HTTPException(400, "context budget must be between 1000 and 1000000 characters")
        if not 1 <= budget["max_files_changed"] <= 100:
            raise HTTPException(400, "file-change budget must be between 1 and 100")

        benchmark_rows: list[dict[str, Any]] = []
        selected: list[dict[str, Any]] = []
        for benchmark_id in body.benchmark_ids:
            benchmark = db.row("SELECT * FROM benchmarks WHERE id=?", (benchmark_id,))
            if (
                not benchmark
                or not can_access_benchmark(user, benchmark)
                or not benchmark["item_count"]
            ):
                raise HTTPException(400, f"benchmark {benchmark_id} is not installed")
            if body.track == "model_direct" and benchmark["task_type"] == "repository_agent":
                raise HTTPException(400, "repository benchmark requires an agent track")
            if body.track in {"native_agent", "reference_agent"} and benchmark["task_type"] != "repository_agent":
                raise HTTPException(400, "agent tracks require repository benchmarks")
            benchmark_rows.append(benchmark)
            selected.extend(
                select_items(
                    db,
                    benchmark_id,
                    body.sample_limit,
                    body.sampling_strategy,
                    body.random_seed,
                    shared_only=(
                        not bool(benchmark.get("official"))
                        and benchmark.get("owner_user_id") != user["id"]
                        and user["role"] != "admin"
                    ),
                )
            )
        protocol = {
            "track": body.track,
            "track_description": TRACKS[body.track],
            "random_seed": body.random_seed,
            "sampling_strategy": body.sampling_strategy,
            "repeats": body.repeats,
            "sample_limit_per_benchmark": body.sample_limit,
            "concurrency": body.concurrency,
            "allow_unsafe_code": body.allow_unsafe_code,
            "budget": budget,
        }
        if body.track == "reference_agent":
            protocol["reference_agent"] = {
                "protocol_version": PROTOCOL_VERSION,
                "tool_calls": 0,
                "output_contract": "complete-file-replacement-json",
            }
        manifest = build_manifest(provider_rows, benchmark_rows, protocol)
        experiment_id = db.execute(
            """INSERT INTO experiments(
            name,status,provider_ids_json,benchmark_ids_json,repeats,sample_limit,concurrency,
            allow_unsafe_code,track,random_seed,sampling_strategy,budget_json,manifest_json,
            config_hash,owner_user_id,created_at) VALUES(?,'queued',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                body.name,
                json.dumps(body.provider_ids),
                json.dumps(body.benchmark_ids),
                body.repeats,
                body.sample_limit,
                body.concurrency,
                int(body.allow_unsafe_code),
                body.track,
                body.random_seed,
                body.sampling_strategy,
                json.dumps(budget),
                json.dumps(manifest, ensure_ascii=False),
                manifest["config_hash"],
                user["id"],
                utcnow(),
            ),
        )
        with db.connect() as conn:
            conn.executemany(
                """INSERT INTO experiment_items(
                experiment_id,benchmark_item_id,selection_order) VALUES(?,?,?)""",
                [
                    (experiment_id, item["id"], index)
                    for index, item in enumerate(selected)
                ],
            )
        manager.start(experiment_id)
        audit(
            "experiment.created",
            "experiment",
            experiment_id,
            {"track": body.track, "config_hash": manifest["config_hash"]},
            owner_user_id=user["id"],
        )
        return experiment_detail(request, experiment_id)

    @app.get("/api/experiments")
    def experiments(request: Request) -> list[dict[str, Any]]:
        user = current_user(request)
        if user["role"] == "admin":
            rows = db.rows("SELECT * FROM experiments ORDER BY id DESC")
        else:
            rows = db.rows(
                "SELECT * FROM experiments WHERE owner_user_id=? ORDER BY id DESC",
                (user["id"],),
            )
        return [display_experiment_name(row) for row in rows]

    @app.get("/api/experiments/{experiment_id}")
    def experiment_detail(request: Request, experiment_id: int) -> dict[str, Any]:
        user = current_user(request)
        exp = db.row("SELECT * FROM experiments WHERE id=?", (experiment_id,))
        if not exp:
            raise HTTPException(404, "experiment not found")
        if not can_access(user, exp["owner_user_id"]):
            raise HTTPException(404, "experiment not found")
        display_experiment_name(exp)
        exp["provider_ids"] = json.loads(exp.pop("provider_ids_json"))
        exp["benchmark_ids"] = json.loads(exp.pop("benchmark_ids_json"))
        exp["budget"] = json.loads(exp.pop("budget_json") or "{}")
        exp["manifest"] = json.loads(exp.pop("manifest_json") or "{}")
        rows = result_rows(experiment_id, 100_000)
        exp["summary"] = summarize_results(rows)
        exp["selected_items"] = db.row(
            "SELECT COUNT(*) n FROM experiment_items WHERE experiment_id=?",
            (experiment_id,),
        )["n"]
        return exp

    @app.get("/api/experiments/{experiment_id}/results")
    def results(
        request: Request, experiment_id: int, limit: int = Query(default=200, le=1000)
    ) -> list[dict[str, Any]]:
        experiment_detail(request, experiment_id)
        return result_rows(experiment_id, limit)

    @app.get("/api/experiments/{experiment_id}/comparison")
    def comparison(request: Request, experiment_id: int) -> dict[str, Any]:
        experiment_detail(request, experiment_id)
        rows = result_rows(experiment_id, 100_000)
        return {"summary": summarize_results(rows), "paired": paired_comparison(rows)}

    @app.get("/api/experiments/{experiment_id}/export")
    def export_experiment(
        request: Request, experiment_id: int, format: str = Query(default="json", pattern="^(json|csv)$")
    ) -> Response:
        user = current_user(request)
        experiment = experiment_detail(request, experiment_id)
        rows = result_rows(experiment_id, 100_000)
        if format == "json":
            content = json.dumps(
                {"experiment": experiment, "results": rows},
                ensure_ascii=False,
                indent=2,
            )
            media_type = "application/json"
        else:
            stream = io.StringIO()
            fields = list(rows[0]) if rows else ["experiment_id"]
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            content = stream.getvalue()
            media_type = "text/csv"
        audit(
            "experiment.exported",
            "experiment",
            experiment_id,
            {"format": format, "result_count": len(rows)},
            owner_user_id=experiment["owner_user_id"],
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="prism-experiment-{experiment_id}.{format}"'
            },
        )

    @app.post("/api/experiments/{experiment_id}/cancel")
    def cancel(request: Request, experiment_id: int) -> dict[str, bool]:
        require_write(request)
        experiment = experiment_detail(request, experiment_id)
        db.execute("UPDATE experiments SET cancel_requested=1 WHERE id=?", (experiment_id,))
        audit("experiment.cancel_requested", "experiment", experiment_id, owner_user_id=experiment["owner_user_id"])
        return {"ok": True}

    @app.get("/api/audit")
    def audit_events(request: Request, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
        user = current_user(request)
        sql = "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?"
        args: tuple[Any, ...] = (limit,)
        if user["role"] != "admin":
            sql = "SELECT * FROM audit_events WHERE owner_user_id=? ORDER BY id DESC LIMIT ?"
            args = (user["id"], limit)
        return [
            decode_json_fields(row, "details_json")
            for row in db.rows(sql, args)
        ]

    @app.get("/api/dashboard")
    def dashboard(request: Request) -> dict[str, Any]:
        user = current_user(request)
        owner_filter = "" if user["role"] == "admin" else " WHERE owner_user_id=?"
        owner_args: tuple[Any, ...] = () if user["role"] == "admin" else (user["id"],)
        exp_filter = "" if user["role"] == "admin" else " AND e.owner_user_id=?"
        visible_benchmarks = sum(
            1
            for row in db.rows("SELECT * FROM benchmarks WHERE item_count>0")
            if can_access_benchmark(user, row)
        )
        return {
            "providers": db.row(f"SELECT COUNT(*) n FROM providers{owner_filter}", owner_args)["n"],
            "benchmarks": visible_benchmarks,
            "experiments": db.row(f"SELECT COUNT(*) n FROM experiments{owner_filter}", owner_args)["n"],
            "running": db.row(
                "SELECT COUNT(*) n FROM experiments e WHERE status IN ('queued','running')" + exp_filter,
                owner_args,
            )["n"],
            "total_results": db.row(
                "SELECT COUNT(*) n FROM results r JOIN experiments e ON e.id=r.experiment_id WHERE 1=1" + exp_filter,
                owner_args,
            )["n"],
            "overall_pass_rate": db.row(
                "SELECT COALESCE(AVG(r.passed),0) value FROM results r JOIN experiments e ON e.id=r.experiment_id WHERE 1=1" + exp_filter,
                owner_args,
            )[
                "value"
            ],
            "recent": db.rows(
                "SELECT * FROM experiments e WHERE 1=1" + exp_filter + " ORDER BY id DESC LIMIT 6",
                owner_args,
            ),
        }

    static = Path(__file__).with_name("static")
    app.mount("/assets", StaticFiles(directory=static), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        return FileResponse(
            static / "index.html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    return app


def main() -> None:
    import os
    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("PRISM_HOST", "127.0.0.1"),
        port=int(os.environ.get("PRISM_PORT", "8765")),
        reload=False,
    )
