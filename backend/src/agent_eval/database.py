from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml
from urllib.parse import unquote, urlsplit
from agent_eval.model_config import load_env_secrets


class DatabaseConfigurationError(ValueError):
    """Raised when database collection is enabled but not configured."""


@dataclass(frozen=True)
class DatabaseConfig:
    enabled: bool
    host: str
    port: int
    name: str
    user: str
    password: str
    sslmode: str
    connect_timeout_seconds: int
    trace_enabled: bool
    include_content: bool
    lookaround_seconds: int
    limit: int
    retention_days: int
    max_content_chars: int

    def connection_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.name,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
            "connect_timeout": self.connect_timeout_seconds,
        }


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise DatabaseConfigurationError(f"Configuration must be a mapping: {path}")
    return value


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_database_config(
    project_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> DatabaseConfig:
    config_dir = project_root / "config"
    data = _merge(
        _read_yaml(config_dir / "database.yaml"),
        _read_yaml(config_dir / "local.yaml"),
    )
    database = data.get("database") or {}
    if not isinstance(database, dict):
        raise DatabaseConfigurationError("database configuration must be a mapping")
    trace = database.get("trace") or {}
    if not isinstance(trace, dict):
        raise DatabaseConfigurationError("database.trace must be a mapping")
    privacy = database.get("privacy") or {}
    if not isinstance(privacy, dict):
        raise DatabaseConfigurationError("database.privacy must be a mapping")
    source_environment = environ if environ is not None else os.environ
    secrets = data.get("secrets") or {}
    env_secrets = load_env_secrets(project_root)
    url_env = str(database.get("url_env") or "DATABASE_URL")
    database_url = str(
        source_environment.get(url_env) or secrets.get(url_env) or env_secrets.get(url_env) or ""
    ).strip()
    password_env = str(database.get("password_env") or "LITELLM_DATABASE_PASSWORD")
    password = str(
        source_environment.get(password_env)
        or secrets.get(password_env)
        or env_secrets.get(password_env)
        or ""
    )
    host = str(database.get("host") or "127.0.0.1")
    port = int(database.get("port") or 5432)
    name = str(database.get("name") or "litellm")
    user = str(database.get("user") or "litellm")
    if database_url:
        parsed = urlsplit(database_url)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise DatabaseConfigurationError(f"{url_env} is not a valid PostgreSQL URL")
        host = parsed.hostname
        port = parsed.port or 5432
        name = parsed.path.lstrip("/") or name
        user = unquote(parsed.username or user)
        password = unquote(parsed.password or password)
    enabled = bool(database.get("enabled", False))
    if enabled and not password:
        raise DatabaseConfigurationError(
            f"Database is enabled but {password_env} is missing; set the environment "
            "variable or add it to ignored config/local.yaml"
        )
    return DatabaseConfig(
        enabled=enabled,
        host=host,
        port=port,
        name=name,
        user=user,
        password=password,
        sslmode=str(database.get("sslmode") or "prefer"),
        connect_timeout_seconds=int(database.get("connect_timeout_seconds") or 5),
        trace_enabled=bool(trace.get("enabled", True)),
        include_content=bool(trace.get("include_content", False)),
        lookaround_seconds=max(0, int(trace.get("lookaround_seconds") or 0)),
        limit=max(1, min(5000, int(trace.get("limit") or 500))),
        retention_days=max(1, int(privacy.get("retention_days") or 30)),
        max_content_chars=max(100, int(privacy.get("max_content_chars") or 20000)),
    )


def _driver() -> tuple[Any, Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - installation failure path
        raise RuntimeError("psycopg is not installed; run the platform setup script") from exc
    return psycopg, dict_row


def _is_transient_database_error(exc: Exception) -> bool:
    sqlstate = str(getattr(exc, "sqlstate", "") or "")
    if sqlstate.startswith(("08", "40", "53", "57P", "58")):
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return (
        any(marker in name for marker in ("operational", "interface", "timeout"))
        or any(marker in text for marker in (
            "timeout", "timed out", "connection reset", "connection refused",
            "server closed the connection", "could not connect", "temporarily unavailable",
        ))
    )


def _database_retry(operation, *, max_attempts: int = 4):
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if not _is_transient_database_error(exc) or attempt + 1 == max_attempts:
                raise
            time.sleep(min(4.0, 0.25 * (2 ** attempt)))
    raise RuntimeError(f"PostgreSQL operation failed: {last_error}")


def database_health(project_root: Path) -> dict[str, Any]:
    try:
        config = resolve_database_config(project_root)
        if not config.enabled:
            return {"status": "disabled"}
        psycopg, dict_row = _driver()
        def check() -> Any:
            with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'select current_database() as database, current_user as "user", '
                        '(select count(*) from "LiteLLM_SpendLogs") as spend_log_count'
                    )
                    return cursor.fetchone()
        row = _database_retry(check)
        return {
            "status": "ok",
            "host": config.host,
            "port": config.port,
            **dict(row or {}),
        }
    except Exception as exc:  # health endpoints must return diagnostics, not crash
        return {"status": "error", "error": str(exc)}


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


_SENSITIVE_KEYS = {"authorization", "api_key", "apikey", "password", "secret", "token"}


def _sanitize(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else _sanitize(item, max_chars=max_chars)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item, max_chars=max_chars) for item in value]
    if isinstance(value, str) and len(value) > max_chars:
        return value[:max_chars] + "...[TRUNCATED]"
    return value


def fetch_model_interactions(
    project_root: Path,
    *,
    started_at: datetime,
    finished_at: datetime,
    model: str,
    key_alias: str | None = None,
) -> list[dict[str, Any]]:
    config = resolve_database_config(project_root)
    if not config.enabled or not config.trace_enabled:
        return []
    psycopg, dict_row = _driver()
    start = started_at - timedelta(seconds=config.lookaround_seconds)
    end = finished_at + timedelta(seconds=config.lookaround_seconds)
    content_columns = ", messages, response" if config.include_content else ""
    match_sql = '''(
        metadata->>'user_api_key_alias' = %s
        or metadata->'spend_logs_metadata'->>'user_api_key_alias' = %s
    )''' if key_alias else '''"startTime" between %s and %s
          and (model = %s or model_group = %s or model like %s)'''
    parameters: tuple[Any, ...] = (key_alias, key_alias) if key_alias else (
        start, end, model, model, f"%{model}%"
    )
    query = f'''select request_id, call_type, spend, total_tokens, prompt_tokens,
        completion_tokens, "startTime" as start_time, "endTime" as end_time,
        model, model_id, model_group, custom_llm_provider, session_id, status,
        agent_id, request_duration_ms{content_columns}
        from "LiteLLM_SpendLogs"
        where {match_sql}
        order by "startTime" asc
        limit %s'''
    def fetch() -> list[dict[str, Any]]:
        with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, (*parameters, config.limit))
                return cursor.fetchall()
    rows = _database_retry(fetch)
    return [
        _sanitize({key: _json_value(value) for key, value in row.items()}, max_chars=config.max_content_chars)
        for row in rows
    ]


def summarize_model_interactions(
    rows: list[dict[str, Any]], *, exact: bool = False
) -> dict[str, Any]:
    successes = sum(str(row.get("status", "")).lower() == "success" for row in rows)
    durations = [int(row["request_duration_ms"]) for row in rows if row.get("request_duration_ms") is not None]
    return {
        "status": "matched" if rows else "no_match",
        "correlation": "run_scoped_virtual_key" if exact else "model_and_time_window",
        "model_call_count": len(rows),
        "successful_model_calls": successes,
        "model_call_success_rate": round(100 * successes / len(rows), 2) if rows else None,
        "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in rows),
        "completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in rows),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in rows),
        "max_prompt_tokens": max(
            (int(row.get("prompt_tokens") or 0) for row in rows), default=None
        ),
        "models": sorted({str(row.get("model")) for row in rows if row.get("model")}),
        "spend": round(sum(float(row.get("spend") or 0) for row in rows), 10),
        "average_request_duration_ms": round(sum(durations) / len(durations), 2)
        if durations
        else None,
    }


def verify_requested_model(
    rows: list[dict[str, Any]],
    *,
    expected_model: str,
    accepted_model_groups: list[str] | None = None,
    exact: bool,
) -> dict[str, Any]:
    """Prove that a run reached the requested LiteLLM model.

    A model/time-window match is useful diagnostic evidence, but only an exact
    run key can attribute the call to this Agent evaluation. Concurrent runs can
    otherwise see one another's SpendLogs rows.
    """
    groups = {expected_model.lower()}
    groups.update(str(item).lower() for item in accepted_model_groups or [] if item)
    expected_leaf = expected_model.rsplit("/", 1)[-1].lower()

    def matches(row: dict[str, Any]) -> bool:
        model = str(row.get("model") or "").lower()
        group = str(row.get("model_group") or "").lower()
        return (
            group in groups
            or model == expected_model.lower()
            or model.endswith(f"/{expected_leaf}")
        )

    successful = [row for row in rows if str(row.get("status") or "").lower() == "success"]
    mismatches = [
        {
            "request_id": row.get("request_id"),
            "model": row.get("model"),
            "model_group": row.get("model_group"),
            "model_id": row.get("model_id"),
        }
        for row in rows
        if not matches(row)
    ]
    model_matched = bool(
        successful and not mismatches and all(matches(row) for row in successful)
    )
    verified = bool(model_matched and exact)
    if not rows:
        reason = "no_database_interactions"
    elif not successful:
        reason = "no_successful_model_call"
    elif mismatches:
        reason = "requested_model_mismatch"
    elif not exact:
        reason = "exact_run_correlation_unavailable"
    else:
        reason = None
    return {
        "status": "verified" if verified else (
            "matched_unattributed" if model_matched else "unverified"
        ),
        "verified": verified,
        "model_matched": model_matched,
        "agent_attribution": "exact_run_key" if exact else "model_and_time_window",
        "exact_agent_attribution": exact,
        "expected_model": expected_model,
        "accepted_model_groups": sorted(groups),
        "successful_matching_calls": sum(matches(row) for row in successful),
        "mismatches": mismatches,
        "reason": reason,
        "warning": None if exact or not model_matched else (
            "The model matched only by time window; the call cannot be attributed to this run"
        ),
    }
