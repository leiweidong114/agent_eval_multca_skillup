from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    task_id: str | None = None,
    user_id: str | None = None,
    agent: str | None = None,
    requested_model: str | None = None,
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
        agent_id, request_duration_ms, "user" as user_id, end_user,
        metadata, proxy_server_request{content_columns}
        from "LiteLLM_SpendLogs"
        where {match_sql}
        order by "startTime" asc
        limit %s offset %s'''
    def fetch() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                offset = 0
                while True:
                    cursor.execute(query, (*parameters, config.limit, offset))
                    page = list(cursor.fetchall())
                    rows.extend(page)
                    if len(page) < config.limit:
                        break
                    offset += len(page)
        return rows
    rows = _database_retry(fetch)
    interactions = [
        _sanitize({key: _json_value(value) for key, value in row.items()}, max_chars=config.max_content_chars)
        for row in rows
    ]
    for row in interactions:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        proxy_request = row.get("proxy_server_request")
        proxy_metadata = (
            proxy_request.get("metadata", {}) if isinstance(proxy_request, dict) else {}
        )
        nested = metadata.get("spend_logs_metadata", {}) if isinstance(metadata, dict) else {}
        sources = [proxy_metadata, metadata, nested]
        def first(name: str) -> Any:
            return next(
                (source.get(name) for source in sources if isinstance(source, dict) and source.get(name) is not None),
                None,
            )
        session = row.get("session_id") or first("session_id")
        parent = first("parent_session_id") or first("parent_session_key") or first("spawned_by")
        session_key = first("session_key")
        structurally_subagent = bool(
            parent or (isinstance(session_key, str) and ":subagent:" in session_key.lower())
        )
        row.update({
            "evaluation_task_id": task_id or first("agent_eval_task_id"),
            "evaluation_run_id": first("agent_eval_run_id"),
            "employee_no": user_id or first("agent_eval_user_id") or row.get("user_id"),
            "key_alias": key_alias or first("user_api_key_alias"),
            "top_level_agent": agent or first("agent_eval_agent"),
            "requested_model": requested_model or first("agent_eval_model"),
            "session_id": session,
            "session_key": session_key,
            "parent_session_id": parent,
            "request_purpose": first("request_purpose"),
            "agent_role": "subagent" if structurally_subagent else "main",
            "agent_role_detection": (
                "structural_parent" if structurally_subagent
                else ("structural_session" if session else "evaluator_default")
            ),
        })
    return interactions


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
            or metadata->>'agent_eval_user_id' = %s
            or proxy_server_request->'metadata'->>'agent_eval_user_id' = %s
        )''')
        parameters.extend([user_id] * 6)
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
    safe_limit = max(1, min(int(limit), 100000))
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
    previous_messages: dict[str, list[dict[str, Any]]] = {}
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
        messages = _request_messages(row)
        session_key = str(row.get("session_id") or f"{scope}:{subagent_name or ''}")
        previous = previous_messages.get(session_key, [])
        if previous:
            common = 0
            for old, current in zip(previous, messages):
                if json.dumps(old, ensure_ascii=False, sort_keys=True, default=str) != json.dumps(
                    current, ensure_ascii=False, sort_keys=True, default=str
                ):
                    break
                common += 1
            current_input = messages[common:]
            history_count = common
        else:
            non_system_indexes = [
                position for position, message in enumerate(messages)
                if str(message.get("role") or "").lower() not in {"system", "developer"}
            ]
            first_current = non_system_indexes[-1] if non_system_indexes else len(messages)
            current_input = messages[first_current:] if first_current < len(messages) else []
            history_count = first_current
        row["current_input_messages"] = current_input
        row["history_message_count"] = history_count
        previous_messages[session_key] = messages
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


def _conversation_user_filter(user_id: str | None) -> tuple[str, list[Any]]:
    normalized = (user_id or "").strip()
    if not normalized:
        return "true", []
    return '''(
        "user" = %s or end_user = %s
        or metadata->>'user_api_key_user_id' = %s
        or metadata->'spend_logs_metadata'->>'user_api_key_user_id' = %s
        or metadata->>'agent_eval_user_id' = %s
        or proxy_server_request->'metadata'->>'agent_eval_user_id' = %s
    )''', [normalized] * 6


def conversation_time_window(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    *,
    default_hours: int = 24,
) -> tuple[datetime, datetime]:
    """Normalize the bounded UTC window used by conversation-facing queries."""
    end = end_time or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = start_time or (end - timedelta(hours=default_hours))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if start >= end:
        raise ValueError("start_time must be earlier than end_time")
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _interaction_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the session lineage LiteLLM records in its JSON columns."""
    metadata = _as_json(row.get("metadata"))
    metadata = metadata if isinstance(metadata, dict) else {}
    proxy_request = _as_json(row.get("proxy_server_request"))
    proxy_request = proxy_request if isinstance(proxy_request, dict) else {}
    proxy_metadata = _as_json(proxy_request.get("metadata"))
    proxy_metadata = proxy_metadata if isinstance(proxy_metadata, dict) else {}
    nested = _as_json(metadata.get("spend_logs_metadata"))
    nested = nested if isinstance(nested, dict) else {}
    sources = (proxy_metadata, metadata, nested)

    def first(*names: str) -> Any:
        for source in sources:
            for name in names:
                value = source.get(name)
                if value not in (None, ""):
                    return value
        return None

    session_id = row.get("session_id") or first("session_id")
    request_id = str(row.get("request_id") or "unknown")
    session_id = str(session_id) if session_id else f"unattributed:{request_id}"
    task_id = row.get("evaluation_task_id") or first("agent_eval_task_id")
    run_id = row.get("evaluation_run_id") or first("agent_eval_run_id")
    key_alias = row.get("key_alias") or first("user_api_key_alias")
    is_evaluation = bool(
        task_id
        or run_id
        or (isinstance(key_alias, str) and "agent-eval" in key_alias.casefold())
    )
    return {
        "session_id": session_id,
        "session_key": row.get("session_key") or first("session_key"),
        "parent_session_id": row.get("parent_session_id") or first("parent_session_id"),
        "parent_session_key": row.get("parent_session_key") or first("parent_session_key"),
        "spawned_by": row.get("spawned_by") or first("spawned_by", "spawnedBy"),
        "evaluation_task_id": task_id,
        "evaluation_run_id": run_id,
        "attributed_user_id": first("agent_eval_user_id", "user_api_key_user_id"),
        "top_level_agent": row.get("top_level_agent") or first("agent_eval_agent") or row.get("agent_id"),
        "requested_model": row.get("requested_model") or first("agent_eval_model"),
        "request_purpose": row.get("request_purpose") or first("request_purpose"),
        "key_alias": key_alias,
        "source_kind": "evaluation" if is_evaluation else "non_evaluation",
    }


def build_conversation_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build root conversations from main-agent and descendant session records."""
    normalized: list[dict[str, Any]] = []
    key_to_session: dict[str, str] = {}
    for source_row in rows:
        row = dict(source_row)
        if "tool_call_count" not in row:
            tool_calls = _response_tool_calls(row.get("response"))
            row["tool_call_count"] = len(tool_calls)
            row["subagent_start_count"] = sum(
                _is_subagent_tool(name, arguments) for name, arguments in tool_calls
            )
        row.update(_interaction_metadata(row))
        if row.get("session_key"):
            key_to_session[str(row["session_key"])] = str(row["session_id"])
        normalized.append(row)

    parents: dict[str, str] = {}
    for row in normalized:
        child = str(row["session_id"])
        parent = row.get("parent_session_id")
        if not parent:
            parent_key = row.get("parent_session_key") or row.get("spawned_by")
            parent = key_to_session.get(str(parent_key)) if parent_key else None
        if parent and str(parent) != child:
            parents.setdefault(child, str(parent))

    def lineage(session_id: str) -> tuple[str, int, bool]:
        current, depth, seen = session_id, 0, {session_id}
        while current in parents and depth < 32:
            parent = parents[current]
            if parent in seen:
                return session_id, depth, True
            seen.add(parent)
            current, depth = parent, depth + 1
        return current, depth, depth >= 32

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in normalized:
        root, depth, invalid_lineage = lineage(str(row["session_id"]))
        row.update({
            "root_session_id": root,
            "depth": depth,
            "agent_role": "subagent" if depth else "main",
            "lineage_invalid": invalid_lineage,
        })
        grouped.setdefault(root, []).append(row)

    conversations: list[dict[str, Any]] = []
    for root, items in grouped.items():
        items.sort(key=lambda item: (str(item.get("start_time") or ""), str(item.get("request_id") or "")))
        by_session: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_session.setdefault(str(item["session_id"]), []).append(item)
        nodes: list[dict[str, Any]] = []
        for session, session_items in by_session.items():
            first = session_items[0]
            node_summary = summarize_interaction_rows(session_items)
            node_summary.update({
                "session_id": session,
                "parent_session_id": parents.get(session),
                "depth": first["depth"],
                "agent_role": first["agent_role"],
                "subagent_name": next(
                    (
                        item.get("subagent_name")
                        for item in session_items
                        if item.get("subagent_name")
                    ),
                    None,
                ),
                "models": sorted({
                    str(item.get("model_group") or item.get("model"))
                    for item in session_items if item.get("model_group") or item.get("model")
                }),
            })
            nodes.append(node_summary)
        nodes.sort(key=lambda node: (int(node["depth"]), str(node.get("started_at") or "")))
        summary = summarize_interaction_rows(items)
        source_kinds = {str(item["source_kind"]) for item in items}
        summary.update({
            "root_session_id": root,
            "session_id": root,
            "source_kind": next(iter(source_kinds)) if len(source_kinds) == 1 else "mixed",
            "user_id": next(
                (item.get("attributed_user_id") for item in items if item.get("attributed_user_id")),
                None,
            ) or next((item.get("user_id") for item in items if item.get("user_id")), None),
            "end_user": next((item.get("end_user") for item in items if item.get("end_user")), None),
            "agent": next((item.get("top_level_agent") for item in items if item.get("top_level_agent")), None),
            "models": sorted({
                str(item.get("model_group") or item.get("model") or item.get("requested_model"))
                for item in items
                if item.get("model_group") or item.get("model") or item.get("requested_model")
            }),
            "evaluation_task_ids": sorted({str(item["evaluation_task_id"]) for item in items if item.get("evaluation_task_id")}),
            "evaluation_run_ids": sorted({str(item["evaluation_run_id"]) for item in items if item.get("evaluation_run_id")}),
            "subagent_count": sum(node["agent_role"] == "subagent" for node in nodes),
            "sessions": nodes,
            "interactions": items,
        })
        conversations.append(summary)
    return sorted(
        conversations,
        key=lambda item: (str(item.get("finished_at") or item.get("started_at") or ""), item["root_session_id"]),
        reverse=True,
    )


def _conversation_session_expression(prefix: str = "") -> str:
    qualified = f"{prefix}." if prefix else ""
    return f"""coalesce({qualified}session_id,
        {qualified}proxy_server_request->'metadata'->>'session_id',
        {qualified}metadata->>'session_id',
        {qualified}metadata->'spend_logs_metadata'->>'session_id')"""


def _conversation_parent_expression(prefix: str = "") -> str:
    qualified = f"{prefix}." if prefix else ""
    return f"""coalesce({qualified}proxy_server_request->'metadata'->>'parent_session_id',
        {qualified}metadata->>'parent_session_id',
        {qualified}metadata->'spend_logs_metadata'->>'parent_session_id')"""


def _conversation_session_key_expression(prefix: str = "") -> str:
    qualified = f"{prefix}." if prefix else ""
    return f"""coalesce({qualified}proxy_server_request->'metadata'->>'session_key',
        {qualified}metadata->>'session_key',
        {qualified}metadata->'spend_logs_metadata'->>'session_key')"""


def _conversation_parent_key_expression(prefix: str = "") -> str:
    qualified = f"{prefix}." if prefix else ""
    return f"""coalesce({qualified}proxy_server_request->'metadata'->>'parent_session_key',
        {qualified}metadata->>'parent_session_key',
        {qualified}metadata->'spend_logs_metadata'->>'parent_session_key',
        {qualified}proxy_server_request->'metadata'->>'spawned_by',
        {qualified}proxy_server_request->'metadata'->>'spawnedBy',
        {qualified}metadata->>'spawned_by', {qualified}metadata->>'spawnedBy')"""


def _conversation_row_columns(*, include_content: bool) -> str:
    content_columns = (
        ", messages, response, proxy_server_request, metadata"
        if include_content
        else """, null::jsonb as messages, null::jsonb as response,
            jsonb_build_object('metadata', proxy_server_request->'metadata') as proxy_server_request,
            metadata"""
    )
    return f'''request_id, call_type, "user" as user_id, end_user,
        "startTime" as start_time, "endTime" as end_time, model, model_group,
        custom_llm_provider, {_conversation_session_expression()} as session_id,
        {_conversation_parent_expression()} as parent_session_id,
        {_conversation_session_key_expression()} as session_key,
        {_conversation_parent_key_expression()} as parent_session_key,
        status, agent_id, request_duration_ms, prompt_tokens,
        completion_tokens, total_tokens, spend {content_columns}'''


def _load_conversation_rows(
    cursor: Any,
    *,
    requested_session_id: str,
    user_clause: str,
    user_parameters: list[Any],
    window_start: datetime,
    window_end: datetime,
    include_content: bool,
    exclude_judge: bool,
) -> tuple[str, list[dict[str, Any]]]:
    """Load one session family without materializing every edge in the time window."""
    session_expression = _conversation_session_expression()
    parent_expression = _conversation_parent_expression()
    session_key_expression = _conversation_session_key_expression()
    parent_key_expression = _conversation_parent_key_expression()
    purpose_clause = ""
    if exclude_judge:
        purpose_clause = '''and coalesce(
            proxy_server_request->'metadata'->>'request_purpose',
            metadata->>'request_purpose',
            metadata->'spend_logs_metadata'->>'request_purpose', '') <> 'llm_judge' '''
    columns = _conversation_row_columns(include_content=include_content)

    def fetch_sessions(session_ids: set[str]) -> list[dict[str, Any]]:
        if not session_ids:
            return []
        values = sorted(session_ids)
        cursor.execute(
            f'''select {columns} from "LiteLLM_SpendLogs"
                where {user_clause}
                and "startTime" >= %s and "startTime" < %s
                {purpose_clause}
                and (session_id = any(%s)
                    or (session_id is null and {session_expression} = any(%s)))
                order by "startTime" asc, request_id''',
            (*user_parameters, window_start, window_end, values, values),
        )
        return [dict(row) for row in cursor.fetchall()]

    def fetch_session_by_key(session_key: str) -> list[dict[str, Any]]:
        cursor.execute(
            f'''select {columns} from "LiteLLM_SpendLogs"
                where {user_clause}
                and "startTime" >= %s and "startTime" < %s
                {purpose_clause}
                and {session_key_expression} = %s
                order by "startTime" asc, request_id''',
            (*user_parameters, window_start, window_end, session_key),
        )
        return [dict(row) for row in cursor.fetchall()]

    rows_by_request: dict[str, dict[str, Any]] = {}

    def remember(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            rows_by_request[str(row.get("request_id") or len(rows_by_request))] = row

    # Resolve an arbitrary main/subagent identifier to its root. Each exact
    # session lookup can use LiteLLM's session_id index; JSON is only a fallback.
    current_id = requested_session_id
    ancestry_seen: set[str] = set()
    root_id = current_id
    while current_id and current_id not in ancestry_seen and len(ancestry_seen) < 32:
        ancestry_seen.add(current_id)
        current_rows = fetch_sessions({current_id})
        if not current_rows:
            break
        remember(current_rows)
        normalized = _interaction_metadata(current_rows[0])
        root_id = str(normalized.get("session_id") or current_id)
        parent_id = normalized.get("parent_session_id")
        if parent_id and str(parent_id) != root_id:
            current_id = str(parent_id)
            continue
        parent_key = normalized.get("parent_session_key") or normalized.get("spawned_by")
        if parent_key:
            parent_rows = fetch_session_by_key(str(parent_key))
            if parent_rows:
                remember(parent_rows)
                current_id = str(_interaction_metadata(parent_rows[0])["session_id"])
                continue
        break

    # Discover all descendant sessions one level at a time. The edge query only
    # returns identifiers; full rows are then fetched through the indexed
    # session_id path instead of joining every SpendLogs row to a global CTE.
    frontier_ids = {root_id}
    root_rows = fetch_sessions(frontier_ids)
    remember(root_rows)
    frontier_keys = {
        str(_interaction_metadata(row).get("session_key"))
        for row in root_rows
        if _interaction_metadata(row).get("session_key")
    }
    known_ids = set(frontier_ids)
    while frontier_ids and len(known_ids) < 4096:
        known_times: list[datetime] = []
        for row in rows_by_request.values():
            for value in (row.get("start_time"), row.get("end_time")):
                if isinstance(value, datetime):
                    known_times.append(
                        value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
                    )
        # A child is spawned while its parent family is active. Restrict the
        # unindexed JSON parent lookup to that activity window; exact child
        # rows are still loaded across the caller's full requested range.
        edge_start = max(
            window_start,
            (min(known_times) - timedelta(minutes=5)) if known_times else window_start,
        )
        edge_end = min(
            window_end,
            (max(known_times) + timedelta(minutes=5)) if known_times else window_end,
        )
        cursor.execute(
            f'''select distinct {session_expression} as session_id,
                    {session_key_expression} as session_key
                from "LiteLLM_SpendLogs"
                where {user_clause}
                and "startTime" >= %s and "startTime" < %s
                {purpose_clause}
                and ({parent_expression} = any(%s)
                    or {parent_key_expression} = any(%s))
                and {session_expression} is not null''',
            (
                *user_parameters,
                edge_start,
                edge_end,
                sorted(frontier_ids),
                sorted(frontier_keys) or ["__no_session_key__"],
            ),
        )
        children = [dict(row) for row in cursor.fetchall()]
        child_ids = {
            str(row["session_id"])
            for row in children
            if row.get("session_id") and str(row["session_id"]) not in known_ids
        }
        if not child_ids:
            break
        child_rows = fetch_sessions(child_ids)
        remember(child_rows)
        known_ids.update(child_ids)
        frontier_ids = child_ids
        frontier_keys = {
            str(row.get("session_key"))
            for row in children
            if row.get("session_key")
        }

    rows = sorted(
        rows_by_request.values(),
        key=lambda row: (str(row.get("start_time") or ""), str(row.get("request_id") or "")),
    )
    return root_id, rows


def search_conversations(
    project_root: Path,
    *,
    user_id: str | None = None,
    end_user: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    model: str | None = None,
    exclude_single_turn: bool = False,
    source: str = "all",
    limit: int = 30,
    offset: int = 0,
    include_interactions: bool = False,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    allowed_root_session_ids: set[str] | None = None,
    excluded_root_session_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return root conversations in a bounded window; default to the latest 24 hours."""
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if source not in {"all", "evaluation", "non_evaluation"}:
        raise ValueError("source must be all, evaluation or non_evaluation")
    window_start, window_end = conversation_time_window(start_time, end_time)
    config = resolve_database_config(project_root)
    if not config.enabled:
        return {"status": "disabled", "conversations": [], "total": 0}
    user_clause, parameters = _conversation_user_filter(user_id)
    end_user = (end_user or "").strip()
    session_id = (session_id or "").strip()
    request_id = (request_id or "").strip()
    model = (model or "").strip()

    request_lookup = bool(request_id)
    if request_lookup:
        # request_id is LiteLLM_SpendLogs' primary key. Resolve it to the
        # containing session first, then reuse the complete session-family
        # loader so searching one turn returns its main Agent and Subagents.
        purpose_clause = '''and coalesce(
            proxy_server_request->'metadata'->>'request_purpose',
            metadata->>'request_purpose',
            metadata->'spend_logs_metadata'->>'request_purpose', '') <> 'llm_judge' '''
        psycopg, dict_row = _driver()
        with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'''select {_conversation_session_expression()} as session_id
                        from "LiteLLM_SpendLogs"
                        where request_id = %s and {user_clause}
                        and "startTime" >= %s and "startTime" < %s
                        {purpose_clause}
                        limit 1''',
                    (request_id, *parameters, window_start, window_end),
                )
                request_row = cursor.fetchone()
        resolved_session_id = str(request_row.get("session_id") or "") if request_row else ""
        if not resolved_session_id:
            return {
                "status": "ok",
                "query": {
                    "end_user": end_user or None,
                    "session_id": None,
                    "request_id": request_id,
                    "model": model or None,
                    "exclude_single_turn": exclude_single_turn,
                    "source": source,
                    "start_time": window_start.isoformat(),
                    "end_time": window_end.isoformat(),
                },
                "total": 0,
                "count": 0,
                "conversations": [],
                "has_more": False,
                "next_offset": None,
                "scan_truncated": False,
                "scanned_interactions": 0,
                "query_strategy": "indexed_request_family",
            }
        session_id = resolved_session_id

    # Exact session lookup is intentionally handled before the bounded global
    # overview scan. This makes a requested root/subagent complete and avoids
    # losing older turns merely because unrelated traffic filled the scan cap.
    if session_id:
        conversation = get_conversation(
            project_root,
            root_session_id=session_id,
            user_id=user_id,
            include_content=False,
            start_time=window_start,
            end_time=window_end,
            exclude_judge=True,
        )
        conversations: list[dict[str, Any]] = []
        if conversation is not None:
            interactions = conversation.pop("timeline", [])
            conversation["interactions"] = interactions
            conversations.append(conversation)
        filtered = []
        for item in conversations:
            root_id = str(item.get("root_session_id") or "")
            if allowed_root_session_ids is not None and root_id not in allowed_root_session_ids:
                continue
            if excluded_root_session_ids is not None and root_id in excluded_root_session_ids:
                continue
            if source != "all" and item["source_kind"] not in {source, "mixed"}:
                continue
            if end_user and not any(str(row.get("end_user") or "") == end_user for row in item["interactions"]):
                continue
            if model and model not in item["models"]:
                continue
            if exclude_single_turn and not request_lookup and int(item.get("interaction_count") or 0) == 1:
                continue
            filtered.append(item)
        total = len(filtered)
        page = filtered[offset:offset + max(1, limit)]
        scanned_interactions = sum(int(item.get("interaction_count") or 0) for item in filtered)
        if not include_interactions:
            for item in page:
                item.pop("interactions", None)
        next_offset = offset + len(page)
        return {
            "status": "ok",
            "query": {
                "end_user": end_user or None,
                "session_id": session_id,
                "request_id": request_id or None,
                "model": model or None,
                "exclude_single_turn": exclude_single_turn,
                "source": source,
                "start_time": window_start.isoformat(),
                "end_time": window_end.isoformat(),
            },
            "total": total,
            "count": len(page),
            "conversations": page,
            "has_more": next_offset < total,
            "next_offset": next_offset if next_offset < total else None,
            "scan_truncated": False,
            "scanned_interactions": scanned_interactions,
            "query_strategy": "indexed_request_family" if request_lookup else "indexed_session_family",
        }

    # `end_user` is the employee number in this deployment.  Filter it in
    # PostgreSQL so the existing LiteLLM_SpendLogs_end_user_idx can be used;
    # fetching an arbitrary recent window and filtering in Python was both
    # slow and incomplete for employees whose sessions fell outside the cap.
    database_filters = [user_clause]
    database_parameters = list(parameters)
    if end_user:
        database_filters.append("end_user = %s")
        database_parameters.append(end_user)
    database_where = " and ".join(f"({clause})" for clause in database_filters)

    # Unfiltered browsing remains bounded as a safety guard.  An exact
    # employee query is already selective and must not silently drop older
    # matching calls, so it has no arbitrary 10k/50k raw-row cap.
    scan_limit = None if end_user else min(50000, max(10000, (offset + max(1, limit)) * 100))
    limit_clause = "" if scan_limit is None else "limit %s"
    query = f'''select request_id, call_type, "user" as user_id, end_user,
        "startTime" as start_time, "endTime" as end_time, model, model_group,
        custom_llm_provider,
        coalesce(session_id, proxy_server_request->'metadata'->>'session_id',
            metadata->>'session_id', metadata->'spend_logs_metadata'->>'session_id') as session_id,
        coalesce(proxy_server_request->'metadata'->>'parent_session_id', metadata->>'parent_session_id',
            metadata->'spend_logs_metadata'->>'parent_session_id') as parent_session_id,
        coalesce(proxy_server_request->'metadata'->>'session_key', metadata->>'session_key',
            metadata->'spend_logs_metadata'->>'session_key') as session_key,
        coalesce(proxy_server_request->'metadata'->>'parent_session_key', metadata->>'parent_session_key',
            metadata->'spend_logs_metadata'->>'parent_session_key') as parent_session_key,
        coalesce(proxy_server_request->'metadata'->>'spawned_by', proxy_server_request->'metadata'->>'spawnedBy',
            metadata->>'spawned_by', metadata->>'spawnedBy') as spawned_by,
        coalesce(proxy_server_request->'metadata'->>'agent_eval_task_id', metadata->>'agent_eval_task_id',
            metadata->'spend_logs_metadata'->>'agent_eval_task_id') as evaluation_task_id,
        coalesce(proxy_server_request->'metadata'->>'agent_eval_run_id', metadata->>'agent_eval_run_id',
            metadata->'spend_logs_metadata'->>'agent_eval_run_id') as evaluation_run_id,
        coalesce(proxy_server_request->'metadata'->>'agent_eval_agent', metadata->>'agent_eval_agent',
            metadata->'spend_logs_metadata'->>'agent_eval_agent') as top_level_agent,
        coalesce(proxy_server_request->'metadata'->>'agent_eval_model', metadata->>'agent_eval_model',
            metadata->'spend_logs_metadata'->>'agent_eval_model') as requested_model,
        coalesce(proxy_server_request->'metadata'->>'request_purpose', metadata->>'request_purpose',
            metadata->'spend_logs_metadata'->>'request_purpose') as request_purpose,
        coalesce(proxy_server_request->'metadata'->>'user_api_key_alias', metadata->>'user_api_key_alias',
            metadata->'spend_logs_metadata'->>'user_api_key_alias') as key_alias,
        status, agent_id, request_duration_ms,
        prompt_tokens, completion_tokens, total_tokens, spend
        from "LiteLLM_SpendLogs" where {database_where}
        and "startTime" >= %s and "startTime" < %s
        and coalesce(proxy_server_request->'metadata'->>'request_purpose',
            metadata->>'request_purpose', metadata->'spend_logs_metadata'->>'request_purpose', '') <> 'llm_judge'
        order by "startTime" desc, request_id {limit_clause}'''
    psycopg, dict_row = _driver()
    with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            query_parameters = [*database_parameters, window_start, window_end]
            if scan_limit is not None:
                query_parameters.append(scan_limit + 1)
            cursor.execute(query, tuple(query_parameters))
            database_rows = cursor.fetchall()
    scan_truncated = scan_limit is not None and len(database_rows) > scan_limit
    if scan_limit is not None:
        database_rows = database_rows[:scan_limit]
    rows = [
        _sanitize({key: _json_value(value) for key, value in row.items()}, max_chars=None)
        for row in database_rows
    ]
    enrich_interaction_rows(rows)
    conversations = build_conversation_groups(rows)

    filtered = []
    for conversation in conversations:
        root_id = str(conversation.get("root_session_id") or "")
        if allowed_root_session_ids is not None and root_id not in allowed_root_session_ids:
            continue
        if excluded_root_session_ids is not None and root_id in excluded_root_session_ids:
            continue
        if source != "all" and conversation["source_kind"] not in {source, "mixed"}:
            continue
        if end_user and not any(str(item.get("end_user") or "") == end_user for item in conversation["interactions"]):
            continue
        if session_id and not any(str(item.get("session_id") or "") == session_id for item in conversation["interactions"]):
            continue
        if model and model not in conversation["models"]:
            continue
        if exclude_single_turn and int(conversation.get("interaction_count") or 0) == 1:
            continue
        filtered.append(conversation)
    total = len(filtered)
    page = filtered[offset:offset + max(1, limit)]
    if not include_interactions:
        for conversation in page:
            conversation.pop("interactions", None)
    next_offset = offset + len(page)
    return {
        "status": "ok",
        "query": {
            "end_user": end_user or None,
            "session_id": session_id or None,
            "request_id": request_id or None,
            "model": model or None,
            "exclude_single_turn": exclude_single_turn,
            "source": source,
            "start_time": window_start.isoformat(),
            "end_time": window_end.isoformat(),
        },
        "total": total,
        "count": len(page),
        "conversations": page,
        "has_more": next_offset < total,
        "next_offset": next_offset if next_offset < total else None,
        "scan_truncated": scan_truncated,
        "scanned_interactions": len(rows),
        "query_strategy": "indexed_end_user_overview" if end_user else "bounded_overview_scan",
    }


def get_conversation(
    project_root: Path,
    *,
    root_session_id: str,
    user_id: str | None = None,
    include_content: bool = True,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    exclude_judge: bool = False,
) -> dict[str, Any] | None:
    """Fetch one root and descendants through targeted session-family lookups."""
    config = resolve_database_config(project_root)
    if not config.enabled:
        return None
    user_clause, parameters = _conversation_user_filter(user_id)
    window_start, window_end = conversation_time_window(start_time, end_time)
    psycopg, dict_row = _driver()
    with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            resolved_root_id, full_rows = _load_conversation_rows(
                cursor,
                requested_session_id=root_session_id,
                user_clause=user_clause,
                user_parameters=parameters,
                window_start=window_start,
                window_end=window_end,
                include_content=include_content,
                exclude_judge=exclude_judge,
            )
    if not full_rows:
        return None
    interactions = [
        _sanitize({key: _json_value(value) for key, value in row.items()}, max_chars=None)
        for row in full_rows
    ]
    enrich_interaction_rows(interactions)
    rebuilt = build_conversation_groups(interactions)
    conversation = next(
        (item for item in rebuilt if item["root_session_id"] == resolved_root_id),
        None,
    )
    if conversation is None:
        return None
    conversation["timeline"] = conversation.pop("interactions")
    conversation["content_loaded"] = include_content
    conversation["query_window"] = {
        "start_time": window_start.isoformat(),
        "end_time": window_end.isoformat(),
    }
    conversation["query_strategy"] = "indexed_session_family"
    conversation["requested_session_id"] = root_session_id
    return conversation


def get_interaction_detail(
    project_root: Path,
    *,
    request_id: str,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """Read one heavy LiteLLM request/response record by its request identifier."""
    config = resolve_database_config(project_root)
    if not config.enabled:
        return None
    user_clause, parameters = _conversation_user_filter(user_id)
    psycopg, dict_row = _driver()
    with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'''select request_id, call_type, "user" as user_id, end_user,
                    "startTime" as start_time, "endTime" as end_time, model, model_group,
                    custom_llm_provider, session_id, status, agent_id, request_duration_ms,
                    prompt_tokens, completion_tokens, total_tokens, spend, messages, response,
                    proxy_server_request, metadata
                from "LiteLLM_SpendLogs"
                where {user_clause} and request_id = %s
                order by "startTime" desc limit 1''', (*parameters, request_id))
            row = cursor.fetchone()
    if not row:
        return None
    result = _sanitize({key: _json_value(value) for key, value in row.items()}, max_chars=None)
    enrich_interaction_rows([result])
    result.update(_interaction_metadata(result))
    return result


def conversation_filter_options(
    project_root: Path,
    *,
    user_id: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """Return LiteLLM End User and model values for overview dropdowns."""
    config = resolve_database_config(project_root)
    if not config.enabled:
        return {"status": "disabled", "end_users": [], "models": []}
    psycopg, dict_row = _driver()
    user_clause, parameters = _conversation_user_filter(user_id)
    window_start, window_end = conversation_time_window(start_time, end_time)
    with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'''select distinct end_user from "LiteLLM_SpendLogs"
                where {user_clause} and "startTime" >= %s and "startTime" < %s
                and coalesce(proxy_server_request->'metadata'->>'request_purpose',
                    metadata->>'request_purpose', metadata->'spend_logs_metadata'->>'request_purpose', '') <> 'llm_judge'
                and end_user is not null and end_user <> '' order by end_user limit 500''',
                (*parameters, window_start, window_end))
            end_users = [str(row["end_user"]) for row in cursor.fetchall()]
            cursor.execute(f'''select distinct coalesce(nullif(model_group, ''), model) as model
                from "LiteLLM_SpendLogs" where {user_clause}
                and "startTime" >= %s and "startTime" < %s
                and coalesce(proxy_server_request->'metadata'->>'request_purpose',
                    metadata->>'request_purpose', metadata->'spend_logs_metadata'->>'request_purpose', '') <> 'llm_judge'
                and coalesce(nullif(model_group, ''), model) is not null
                order by model limit 500''', (*parameters, window_start, window_end))
            models = [str(row["model"]) for row in cursor.fetchall()]
    return {
        "status": "ok",
        "end_users": end_users,
        "models": models,
        "start_time": window_start.isoformat(),
        "end_time": window_end.isoformat(),
    }


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
