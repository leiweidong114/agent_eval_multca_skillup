from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml
from urllib.parse import unquote, urlsplit
from agent_eval.env_config import effective_environment


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


def _env_bool(environment: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environment.get(name)
    if raw is None or not str(raw).strip():
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise DatabaseConfigurationError(f"{name} must be true or false")


def resolve_database_config(
    project_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> DatabaseConfig:
    config_dir = project_root / "config"
    data = _read_yaml(config_dir / "database.yaml")
    database = data.get("database") or {}
    if not isinstance(database, dict):
        raise DatabaseConfigurationError("database configuration must be a mapping")
    trace = database.get("trace") or {}
    if not isinstance(trace, dict):
        raise DatabaseConfigurationError("database.trace must be a mapping")
    privacy = database.get("privacy") or {}
    if not isinstance(privacy, dict):
        raise DatabaseConfigurationError("database.privacy must be a mapping")
    source_environment = effective_environment(project_root, environ)
    url_env = str(database.get("url_env") or "DATABASE_URL")
    database_url = str(source_environment.get(url_env) or "").strip()
    password_env = str(database.get("password_env") or "LITELLM_DATABASE_PASSWORD")
    password = str(
        source_environment.get("DATABASE_PASSWORD")
        or source_environment.get(password_env)
        or ""
    )
    host = str(source_environment.get("DATABASE_HOST") or database.get("host") or "127.0.0.1")
    port = int(source_environment.get("DATABASE_PORT") or database.get("port") or 5432)
    name = str(source_environment.get("DATABASE_NAME") or database.get("name") or "litellm")
    user = str(source_environment.get("DATABASE_USER") or database.get("user") or "litellm")
    if database_url:
        parsed = urlsplit(database_url)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise DatabaseConfigurationError(f"{url_env} is not a valid PostgreSQL URL")
        host = parsed.hostname
        port = parsed.port or 5432
        name = parsed.path.lstrip("/") or name
        user = unquote(parsed.username or user)
        password = unquote(parsed.password or password)
    enabled = _env_bool(
        source_environment, "DATABASE_ENABLED", bool(database_url or database.get("enabled", False))
    )
    if enabled and not password:
        raise DatabaseConfigurationError(
            f"Database is enabled but DATABASE_PASSWORD (or {password_env}) is missing "
            "from repository-root .env"
        )
    return DatabaseConfig(
        enabled=enabled,
        host=host,
        port=port,
        name=name,
        user=user,
        password=password,
        sslmode=str(source_environment.get("DATABASE_SSLMODE") or database.get("sslmode") or "prefer"),
        connect_timeout_seconds=int(source_environment.get("DATABASE_CONNECT_TIMEOUT_SECONDS") or database.get("connect_timeout_seconds") or 5),
        trace_enabled=_env_bool(source_environment, "DATABASE_TRACE_ENABLED", bool(trace.get("enabled", True))),
        include_content=_env_bool(source_environment, "DATABASE_TRACE_INCLUDE_CONTENT", bool(trace.get("include_content", False))),
        lookaround_seconds=max(0, int(source_environment.get("DATABASE_TRACE_LOOKAROUND_SECONDS") or trace.get("lookaround_seconds") or 0)),
        limit=max(1, min(5000, int(source_environment.get("DATABASE_TRACE_LIMIT") or trace.get("limit") or 500))),
        retention_days=max(1, int(source_environment.get("DATABASE_RETENTION_DAYS") or privacy.get("retention_days") or 30)),
        max_content_chars=max(100, int(source_environment.get("DATABASE_MAX_CONTENT_CHARS") or privacy.get("max_content_chars") or 20000)),
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


_SENSITIVE_KEYS = {"authorization", "api_key", "apikey", "password", "secret", "token", "cookie", "set_cookie"}


def _sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized in _SENSITIVE_KEYS
        or "password" in normalized
        or "authorization" in normalized
        or "secret" in normalized
        or ("api_key" in normalized and not normalized.endswith("_alias"))
        or normalized.endswith("_token")
    )


def _sanitize(value: Any, *, max_chars: int | None) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _sensitive_key(key) else _sanitize(item, max_chars=max_chars)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item, max_chars=max_chars) for item in value]
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                return json.dumps(_sanitize(parsed, max_chars=max_chars), ensure_ascii=False)
        except (ValueError, TypeError):
            pass
    if isinstance(value, str) and max_chars is not None and len(value) > max_chars:
        return value[:max_chars] + "...[TRUNCATED]"
    return value


def wait_for_model_interactions(project_root: Path, **kwargs: Any) -> list[dict[str, Any]]:
    """Bounded eventual-consistency wait; never claim database logging is lossless.

    Wait at least 10 seconds and three unchanged snapshots after a success.
    Slow writers may still lag past this one-minute collection window.
    """
    previous = None
    stable = 0
    for attempt in range(31):
        rows = fetch_model_interactions(project_root, **kwargs)
        signature = tuple((r.get("request_id"), r.get("status"), r.get("total_tokens")) for r in rows)
        stable = stable + 1 if signature == previous else 0
        previous = signature
        if attempt >= 5 and stable >= 3 and any(r.get("status") == "success" for r in rows):
            return rows
        if attempt < 30:
            time.sleep(2)
    return rows


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
    content_columns = ", messages, response, proxy_server_request" if config.include_content else ""
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


def search_conversation_interactions(
    project_root: Path,
    *,
    user_id: str | None = None,
    end_user: str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    limit: int = 500,
    offset: int = 0,
    full_content: bool = False,
) -> dict[str, Any]:
    """Search LiteLLM request/response content by user or conversation session."""
    user_id = (user_id or "").strip()
    end_user = (end_user or "").strip()
    session_id = (session_id or "").strip()
    model = (model or "").strip()
    if offset < 0:
        raise ValueError("offset must be non-negative")
    config = resolve_database_config(project_root)
    if not config.enabled:
        return {"status": "disabled", "interactions": [], "sessions": []}
    clauses: list[str] = []
    parameters: list[Any] = []
    if user_id:
        clauses.append('''(
            "user" = %s or end_user = %s
            or metadata->>'user_api_key_user_id' = %s
            or metadata->'spend_logs_metadata'->>'user_api_key_user_id' = %s
        )''')
        parameters.extend([user_id, user_id, user_id, user_id])
    if end_user:
        clauses.append("end_user = %s")
        parameters.append(end_user)
    if session_id:
        clauses.append('''(
            session_id = %s
            or metadata->>'session_id' = %s
            or proxy_server_request->'metadata'->>'session_id' = %s
            or end_user like %s
        )''')
        parameters.extend([session_id, session_id, session_id, f'%"session_id":"{session_id}"%'])
    if model:
        clauses.append("(model = %s or model_group = %s)")
        parameters.extend([model, model])
    query = f'''select request_id, call_type, "user" as user_id, end_user,
        "startTime" as start_time, "endTime" as end_time, model, model_group,
        custom_llm_provider, session_id, status, agent_id, request_duration_ms,
        prompt_tokens, completion_tokens, total_tokens, spend,
        messages, response, proxy_server_request, metadata
        from "LiteLLM_SpendLogs"
        where {' and '.join(clauses) or 'true'}
        order by "startTime" {'asc' if user_id or end_user or session_id or model else 'desc'}, request_id
        limit %s offset %s'''
    session_query = f'''select request_id, "user" as user_id, end_user,
        "startTime" as start_time, "endTime" as end_time, model, model_group,
        session_id, agent_id, total_tokens, response
        from "LiteLLM_SpendLogs"
        where {' and '.join(clauses) or 'true'}
        order by "startTime" desc, request_id
        limit %s'''
    safe_limit = max(1, min(int(limit), config.limit, 1000))
    psycopg, dict_row = _driver()
    with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (*parameters, safe_limit + 1, offset))
            rows = cursor.fetchall()
            cursor.execute(session_query, (*parameters, min(config.limit, 5000)))
            session_rows = cursor.fetchall()
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]
    interactions = [
        _sanitize({key: _json_value(value) for key, value in row.items()}, max_chars=None if full_content else config.max_content_chars)
        for row in rows
    ]
    for index, row in enumerate(interactions, start=offset + 1):
        tool_calls = _response_tool_calls(row.get("response"))
        row["turn_index"] = index
        row["tool_call_count"] = len(tool_calls)
        row["subagent_start_count"] = sum(
            _is_subagent_tool(name, arguments) for name, arguments in tool_calls
        )
    session_interactions = [
        _sanitize({key: _json_value(value) for key, value in row.items()}, max_chars=None)
        for row in session_rows
    ]
    enrich_interaction_rows(session_interactions)
    session_map: dict[str, dict[str, Any]] = {}
    for row in session_interactions:
        sid = str(row.get("session_id") or "未记录会话 ID")
        item = session_map.setdefault(
            sid,
            {
                "session_id": sid,
                "user_id": row.get("user_id"),
                "end_user": row.get("end_user"),
                "agent_id": row.get("agent_id"),
                "models": set(),
                "interaction_count": 0,
                "total_tokens": 0,
                "tool_call_count": 0,
                "subagent_start_count": 0,
                "started_at": row.get("start_time"),
                "finished_at": row.get("end_time"),
            },
        )
        effective_model = row.get("model_group") or row.get("model")
        if effective_model:
            item["models"].add(str(effective_model))
        item["interaction_count"] += 1
        item["total_tokens"] += int(row.get("total_tokens") or 0)
        item["tool_call_count"] += int(row.get("tool_call_count") or 0)
        item["subagent_start_count"] += int(row.get("subagent_start_count") or 0)
        if row.get("start_time") and (not item["started_at"] or str(row["start_time"]) < str(item["started_at"])):
            item["started_at"] = row["start_time"]
        if row.get("end_time") and (not item["finished_at"] or str(row["end_time"]) > str(item["finished_at"])):
            item["finished_at"] = row["end_time"]
    sessions = []
    for item in session_map.values():
        item["models"] = sorted(item["models"])
        item["duration_ms"] = _elapsed_ms(item.get("started_at"), item.get("finished_at"))
        sessions.append(item)
    return {
        "status": "ok",
        "query": {"user_id": user_id or None, "end_user": end_user or None, "session_id": session_id or None, "model": model or None},
        "count": len(interactions),
        "sessions": sessions,
        "interactions": interactions,
        "truncated": has_more,
        "has_more": has_more,
        "offset": offset,
        "next_offset": offset + len(interactions) if has_more else None,
        "content_truncation_enabled": not full_content,
        "content_source": "LiteLLM_SpendLogs; absent upstream content cannot be reconstructed",
    }


def _as_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _response_tool_calls(value: Any) -> list[tuple[str, Any]]:
    """Return only tool calls newly emitted by this model response."""
    calls_found: list[tuple[str, Any]] = []

    def visit(node: Any) -> None:
        node = _as_json(node)
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        calls = node.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = function.get("name") or call.get("name")
                if name:
                    calls_found.append((str(name), function.get("arguments") or call.get("arguments")))
        if node.get("type") in {"function_call", "tool_use"} and node.get("name"):
            calls_found.append((str(node["name"]), node.get("arguments") or node.get("input")))
        for key, child in node.items():
            if key != "tool_calls":
                visit(child)

    visit(value)
    return calls_found


def _response_tool_names(value: Any) -> list[str]:
    return [name for name, _ in _response_tool_calls(value)]


def _is_subagent_tool(name: str, arguments: Any = None) -> bool:
    normalized = name.lower().replace("-", "_")
    base_name = normalized.rsplit("__", 1)[-1]
    if base_name in {
        "sessions_spawn", "spawn_agent", "subagent_spawn", "start_subagent", "agent", "task",
    }:
        return True
    if base_name != "exec":
        return False
    serialized = json.dumps(arguments, ensure_ascii=False, default=str) if not isinstance(arguments, str) else arguments
    return (
        "openclaw agent exec" in serialized
        or "agent-eval check-agent --agent justdo" in serialized
    )


def _request_messages(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return normalized request messages without duplicating large content fields."""
    raw = _as_json(row.get("proxy_server_request"))
    request = raw if isinstance(raw, dict) else {}
    body = _as_json(request.get("body"))
    if isinstance(body, dict):
        request = body
    messages = _as_json(request.get("messages") or row.get("messages") or request.get("input"))
    if not isinstance(messages, list):
        return [{"role": "user", "content": messages}] if isinstance(messages, str) else []
    return [
        message if isinstance(message, dict) else {"role": "user", "content": str(message)}
        for message in messages
    ]


def _message_text(value: Any) -> str:
    value = _as_json(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            _message_text(item.get("text") or item.get("content") or item)
            if isinstance(item, dict) else _message_text(item)
            for item in value
        )
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False, default=str))
    return "" if value is None else str(value)


def _interaction_actor(row: Mapping[str, Any]) -> tuple[str, str | None]:
    """Classify main/child model calls from durable OpenClaw/Codex request evidence."""
    session_id = str(row.get("session_id") or "")
    candidate = "\n".join(
        _message_text(message.get("content"))
        for message in _request_messages(row)
        if str(message.get("role") or "").lower() in {"system", "developer", "user"}
    )
    lowered = candidate.casefold()
    is_subagent = (
        ":subagent:" in session_id.casefold()
        or "[subagent context]" in lowered
        or "[subagent task]" in lowered
        or "you are running as a subagent" in lowered
    )
    if not is_subagent:
        return "main_agent", None
    slice_match = re.search(r"\bslice_id\s*=\s*([A-Za-z0-9_.-]+)", candidate, flags=re.I)
    if slice_match:
        return "subagent", slice_match.group(1)
    task_match = re.search(r"\[Subagent Task\]\s*\n+\s*([^\r\n]+)", candidate, flags=re.I)
    if task_match:
        label = re.sub(r"\s+", " ", task_match.group(1)).strip()
        return "subagent", label[:80] or "未命名子任务"
    return "subagent", "未命名子任务"


def _elapsed_ms(start: Any, end: Any) -> int | None:
    def parse(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    started, finished = parse(start), parse(end)
    if not started or not finished:
        return None
    try:
        return max(0, round((finished - started).total_seconds() * 1000))
    except TypeError:
        return None


def enrich_interaction_rows(rows: list[dict[str, Any]], *, start_index: int = 1) -> list[dict[str, Any]]:
    """Add stable per-turn metrics used by database and durable report views."""
    for index, row in enumerate(rows, start=start_index):
        tool_calls = _response_tool_calls(row.get("response"))
        row["turn_index"] = index
        row["tool_call_count"] = len(tool_calls)
        row["subagent_start_count"] = sum(
            _is_subagent_tool(name, arguments) for name, arguments in tool_calls
        )
        scope, subagent_name = _interaction_actor(row)
        row["interaction_scope"] = scope
        row["subagent_name"] = subagent_name
    return rows


def summarize_interaction_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    starts = [row.get("start_time") for row in rows if row.get("start_time")]
    ends = [row.get("end_time") for row in rows if row.get("end_time")]
    durations = [int(row.get("request_duration_ms") or 0) for row in rows]
    return {
        "interaction_count": len(rows),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in rows),
        "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in rows),
        "completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in rows),
        "tool_call_count": sum(int(row.get("tool_call_count") or 0) for row in rows),
        "subagent_start_count": sum(int(row.get("subagent_start_count") or 0) for row in rows),
        "started_at": min(starts, key=str) if starts else None,
        "finished_at": max(ends, key=str) if ends else None,
        "duration_ms": _elapsed_ms(min(starts, key=str), max(ends, key=str)) if starts and ends else sum(durations),
    }


def group_interaction_sessions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("session_id") or "未记录会话 ID"), []).append(row)
    sessions: list[dict[str, Any]] = []
    for session_id, items in grouped.items():
        summary = summarize_interaction_rows(items)
        summary.update({
            "session_id": session_id,
            "end_user": next((item.get("end_user") for item in items if item.get("end_user")), None),
            "models": sorted({str(item.get("model_group") or item.get("model")) for item in items if item.get("model_group") or item.get("model")}),
        })
        sessions.append(summary)
    return sorted(sessions, key=lambda item: str(item.get("started_at") or ""))


def group_subagent_interactions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize every independently detected child task for result-page filtering."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("interaction_scope") != "subagent":
            continue
        grouped.setdefault(str(row.get("subagent_name") or "未命名子任务"), []).append(row)
    result: list[dict[str, Any]] = []
    for name, items in grouped.items():
        summary = summarize_interaction_rows(items)
        summary.update({"name": name, "interaction_scope": "subagent"})
        result.append(summary)
    return sorted(result, key=lambda item: str(item.get("started_at") or ""))


def conversation_filter_options(project_root: Path) -> dict[str, Any]:
    """Return LiteLLM End User and model values for overview dropdowns."""
    config = resolve_database_config(project_root)
    if not config.enabled:
        return {"status": "disabled", "end_users": [], "models": []}
    psycopg, dict_row = _driver()
    with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute('''select distinct end_user from "LiteLLM_SpendLogs"
                where end_user is not null and end_user <> '' order by end_user limit 500''')
            end_users = [str(row["end_user"]) for row in cursor.fetchall()]
            cursor.execute('''select distinct coalesce(nullif(model_group, ''), model) as model
                from "LiteLLM_SpendLogs" where coalesce(nullif(model_group, ''), model) is not null
                order by model limit 500''')
            models = [str(row["model"]) for row in cursor.fetchall()]
    return {"status": "ok", "end_users": end_users, "models": models}


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
        if group:
            return group in groups
        return (
            group in groups
            or model in groups
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
        for row in successful
        if not matches(row)
    ]
    failed_mismatched_attempts = [
        {
            "request_id": row.get("request_id"),
            "model": row.get("model"),
            "model_group": row.get("model_group"),
            "status": row.get("status"),
        }
        for row in rows
        if row not in successful and not matches(row)
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
        "failed_mismatched_attempts": failed_mismatched_attempts,
        "reason": reason,
        "warning": None if exact or not model_matched else (
            "The model matched only by time window; the call cannot be attributed to this run"
        ),
    }
