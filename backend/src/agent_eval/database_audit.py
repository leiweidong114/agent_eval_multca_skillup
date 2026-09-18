from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from agent_eval.database import _database_retry, _driver, resolve_database_config


DEFAULT_BASELINE = Path(__file__).resolve().parents[2] / "config" / "database-schema-baseline.json"
SPEND_LOG_TABLE = 'public."LiteLLM_SpendLogs"'
REQUIRED_SPEND_LOG_COLUMNS = (
    "request_id",
    "call_type",
    "spend",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "startTime",
    "endTime",
    "model",
    "model_id",
    "model_group",
    "custom_llm_provider",
    "user",
    "metadata",
    "end_user",
    "messages",
    "response",
    "proxy_server_request",
    "session_id",
    "status",
    "agent_id",
    "request_duration_ms",
)


def database_content_issues(spend_format: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe whether recent SpendLogs can drive trace verification and the UI.

    Only aggregate counts and key names are inspected. Prompt, response and metadata
    values are deliberately excluded from this diagnostic path.
    """
    if not spend_format.get("exists") or not spend_format.get("selectable"):
        return []
    sample = spend_format.get("latest_sample") or {}
    sampled_rows = int(sample.get("sampled_rows") or 0)
    issues: list[dict[str, Any]] = []
    if sampled_rows == 0:
        return [{
            "severity": "error",
            "category": "database_trace_table_empty",
            "summary": "LiteLLM_SpendLogs contains no interactions",
            "detail": (
                "The database connection and schema work, but there are no rows for model "
                "verification, the schematic overview, or conversation details."
            ),
            "suggested_action": (
                "Confirm that LiteLLM writes to this same PostgreSQL database, make one real "
                "model call, then rerun self-test.ps1 -Strict."
            ),
            "configuration_changes": [],
        }]

    def missing(field: str) -> bool:
        return int(sample.get(field) or 0) == 0

    if missing("model_rows"):
        issues.append({
            "severity": "error",
            "category": "database_model_content_missing",
            "summary": "Recent SpendLogs rows contain no model identity",
            "detail": "Model attribution and model filters cannot work without model values.",
            "suggested_action": "Check LiteLLM spend-log writing and the database migration version.",
            "configuration_changes": [],
        })
    if missing("messages_rows"):
        issues.append({
            "severity": "warning",
            "category": "database_messages_content_missing",
            "summary": "Recent SpendLogs rows contain no stored input messages",
            "detail": "The UI can list calls, but cannot reconstruct System/History/User input.",
            "suggested_action": "Check LiteLLM content logging and privacy/redaction settings.",
            "configuration_changes": [],
        })
    if missing("response_rows"):
        issues.append({
            "severity": "warning",
            "category": "database_response_content_missing",
            "summary": "Recent SpendLogs rows contain no stored model responses",
            "detail": "The UI can list calls, but cannot display model output.",
            "suggested_action": "Check LiteLLM content logging and privacy/redaction settings.",
            "configuration_changes": [],
        })
    if missing("session_rows"):
        issues.append({
            "severity": "warning",
            "category": "database_session_content_missing",
            "summary": "Recent SpendLogs rows contain no session_id",
            "detail": "Calls cannot be grouped reliably into multi-turn Agent sessions.",
            "suggested_action": (
                "Confirm that the Agent adapter sends session_id in the request body or metadata."
            ),
            "configuration_changes": [],
        })
    if missing("end_user_rows"):
        issues.append({
            "severity": "warning",
            "category": "database_end_user_content_missing",
            "summary": "Recent SpendLogs rows contain no end_user",
            "detail": "The schematic overview cannot filter these interactions by employee/user.",
            "suggested_action": (
                "Confirm that gateway requests include LiteLLM end_user/user and that the "
                "Agent adapter forwards the configured employee number."
            ),
            "configuration_changes": [],
        })
    if missing("token_rows"):
        issues.append({
            "severity": "warning",
            "category": "database_token_content_missing",
            "summary": "Recent SpendLogs rows contain no positive token totals",
            "detail": "Token statistics in overview and evaluation reports will be incomplete.",
            "suggested_action": "Confirm that the upstream provider returns usage and LiteLLM stores it.",
            "configuration_changes": [],
        })
    return issues


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _table_key(table: dict[str, Any]) -> str:
    return f"{table.get('schema')}.{table.get('name')}"


def compare_database_schema(
    inventory: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    """Compare structural metadata without comparing database contents."""
    current_tables = {_table_key(table): table for table in inventory.get("tables", [])}
    baseline_tables = {_table_key(table): table for table in baseline.get("tables", [])}
    missing_tables: list[str] = []
    table_differences: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for key, expected in baseline_tables.items():
        actual = current_tables.get(key)
        if actual is None:
            missing_tables.append(key)
            issues.append({
                "severity": "error",
                "category": "database_table_missing",
                "summary": f"Required table is missing: {key}",
                "detail": "The evaluation system cannot read LiteLLM interaction traces.",
                "suggested_action": (
                    "Run the database migrations shipped with the same LiteLLM version as the "
                    "working environment; do not create an empty replacement table manually."
                ),
                "configuration_changes": [
                    "Point DATABASE_URL/DATABASE_HOST at the LiteLLM PostgreSQL database.",
                    "Upgrade or migrate the intranet LiteLLM database to a compatible schema.",
                ],
            })
            continue

        expected_columns = {column["name"]: column for column in expected.get("columns", [])}
        actual_columns = {column["name"]: column for column in actual.get("columns", [])}
        missing_columns = sorted(set(expected_columns) - set(actual_columns))
        extra_columns = sorted(set(actual_columns) - set(expected_columns))
        changed_columns: list[dict[str, Any]] = []
        for name in sorted(set(expected_columns) & set(actual_columns)):
            expected_column = expected_columns[name]
            actual_column = actual_columns[name]
            changes = {}
            for field in ("data_type", "udt_name", "nullable"):
                if expected_column.get(field) != actual_column.get(field):
                    changes[field] = {
                        "expected": expected_column.get(field),
                        "actual": actual_column.get(field),
                    }
            if changes:
                changed_columns.append({"name": name, "changes": changes})

        expected_indexes = {item["name"]: item for item in expected.get("indexes", [])}
        actual_indexes = {item["name"]: item for item in actual.get("indexes", [])}
        missing_indexes = sorted(set(expected_indexes) - set(actual_indexes))
        changed_indexes = sorted(
            name for name in set(expected_indexes) & set(actual_indexes)
            if expected_indexes[name].get("definition") != actual_indexes[name].get("definition")
        )
        difference = {
            "table": key,
            "missing_columns": missing_columns,
            "extra_columns": extra_columns,
            "changed_columns": changed_columns,
            "missing_indexes": missing_indexes,
            "changed_indexes": changed_indexes,
            "selectable": bool(actual.get("selectable")),
        }
        table_differences.append(difference)

        if not actual.get("selectable"):
            issues.append({
                "severity": "error",
                "category": "database_select_permission_missing",
                "summary": f"DATABASE_USER cannot SELECT from {key}",
                "detail": "Connection succeeds, but trace rows cannot be queried.",
                "suggested_action": (
                    f'Run as a database administrator: GRANT SELECT ON TABLE {SPEND_LOG_TABLE} '
                    'TO <DATABASE_USER>;'
                ),
                "configuration_changes": [
                    "Use a DATABASE_USER with read access to LiteLLM_SpendLogs.",
                ],
            })
        if missing_columns:
            required_missing = sorted(set(missing_columns) & set(REQUIRED_SPEND_LOG_COLUMNS))
            issues.append({
                "severity": "error" if required_missing else "warning",
                "category": "database_columns_missing",
                "summary": f"{key} is missing {len(missing_columns)} baseline columns",
                "detail": ", ".join(missing_columns),
                "suggested_action": (
                    "Align the LiteLLM application and database migration versions. Missing "
                    "columns used by the evaluator must be added by the official LiteLLM migration."
                ),
                "configuration_changes": [
                    "Use the same LiteLLM release/migrations as the baseline environment.",
                ],
            })
        type_changes = [
            item for item in changed_columns
            if "data_type" in item["changes"] or "udt_name" in item["changes"]
        ]
        nullable_changes = [
            item for item in changed_columns
            if set(item["changes"]) == {"nullable"}
        ]
        if type_changes:
            issues.append({
                "severity": "error",
                "category": "database_column_type_mismatch",
                "summary": f"{key} has incompatible column definitions",
                "detail": json.dumps(type_changes, ensure_ascii=False),
                "suggested_action": (
                    "Run the matching LiteLLM database migration and retest before evaluation."
                ),
                "configuration_changes": [
                    "Do not patch JSON/timestamp columns ad hoc; apply versioned LiteLLM migrations.",
                ],
            })
        if nullable_changes:
            issues.append({
                "severity": "warning",
                "category": "database_column_nullability_difference",
                "summary": f"{key} column nullability differs from the baseline",
                "detail": json.dumps(nullable_changes, ensure_ascii=False),
                "suggested_action": (
                    "Review the LiteLLM migration version. This does not block reads, but it "
                    "indicates schema drift from the working environment."
                ),
                "configuration_changes": [],
            })
        if missing_indexes or changed_indexes:
            issues.append({
                "severity": "warning",
                "category": "database_index_difference",
                "summary": f"{key} indexes differ from the working environment",
                "detail": json.dumps({
                    "missing_indexes": missing_indexes,
                    "changed_indexes": changed_indexes,
                }, ensure_ascii=False),
                "suggested_action": (
                    "Apply the matching migration. Missing startTime/session_id indexes may make "
                    "trace collection and conversation pages slow."
                ),
                "configuration_changes": [],
            })

    compatible = not any(issue["severity"] == "error" for issue in issues)
    return {
        "compatible": compatible,
        "baseline_source": baseline.get("source"),
        "missing_tables": missing_tables,
        # The committed compatibility baseline intentionally contains only tables
        # read by this evaluator. Other LiteLLM tables are inventory context, not
        # schema errors and should not be described as unexpected/extra tables.
        "unbaselined_tables": sorted(set(current_tables) - set(baseline_tables)),
        "table_differences": table_differences,
        "issues": issues,
    }


def _load_baseline(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("tables"), list):
        raise ValueError(f"Invalid database schema baseline: {path}")
    return data


def audit_database_schema(
    project_root: Path,
    *,
    baseline_path: Path | None = None,
) -> dict[str, Any]:
    """Inspect PostgreSQL metadata and compare the trace table with the public baseline."""
    try:
        config = resolve_database_config(project_root)
        if not config.enabled:
            return {
                "status": "disabled",
                "issues": [{
                    "severity": "error",
                    "category": "database_disabled",
                    "summary": "Database trace collection is disabled",
                    "detail": "DATABASE_ENABLED is false.",
                    "suggested_action": "Set DATABASE_ENABLED=true and configure the intranet PostgreSQL connection.",
                    "configuration_changes": ["DATABASE_ENABLED=true"],
                }],
            }
        selected_baseline = (baseline_path or DEFAULT_BASELINE).resolve()
        baseline = _load_baseline(selected_baseline)
        psycopg, dict_row = _driver()

        def inspect() -> dict[str, Any]:
            with psycopg.connect(
                **config.connection_kwargs(), row_factory=dict_row
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "select current_database() as database, current_user as \"user\", "
                        "current_schema() as current_schema, "
                        "current_setting('server_version') as server_version"
                    )
                    server = dict(cursor.fetchone() or {})
                    cursor.execute("""
                        select tables.table_schema, tables.table_name,
                               columns.ordinal_position, columns.column_name,
                               columns.data_type, columns.udt_name, columns.is_nullable
                        from information_schema.tables as tables
                        join information_schema.columns as columns
                          on columns.table_schema = tables.table_schema
                         and columns.table_name = tables.table_name
                        where tables.table_type = 'BASE TABLE'
                          and tables.table_schema not in ('pg_catalog', 'information_schema')
                        order by tables.table_schema, tables.table_name, columns.ordinal_position
                    """)
                    column_rows = list(cursor.fetchall())
                    cursor.execute("""
                        select namespace.nspname as table_schema,
                               class.relname as table_name,
                               has_table_privilege(class.oid, 'SELECT') as selectable
                        from pg_class as class
                        join pg_namespace as namespace on namespace.oid = class.relnamespace
                        where class.relkind in ('r', 'p')
                          and namespace.nspname not in ('pg_catalog', 'information_schema')
                        order by namespace.nspname, class.relname
                    """)
                    permissions = {
                        (row["table_schema"], row["table_name"]): bool(row["selectable"])
                        for row in cursor.fetchall()
                    }
                    cursor.execute("""
                        select schemaname as table_schema, tablename as table_name,
                               indexname as index_name, indexdef as index_definition
                        from pg_indexes
                        where schemaname not in ('pg_catalog', 'information_schema')
                        order by schemaname, tablename, indexname
                    """)
                    index_rows = list(cursor.fetchall())

                    tables: dict[tuple[str, str], dict[str, Any]] = {}
                    for row in column_rows:
                        key = (row["table_schema"], row["table_name"])
                        table = tables.setdefault(key, {
                            "schema": row["table_schema"],
                            "name": row["table_name"],
                            "selectable": permissions.get(key, False),
                            "columns": [],
                            "indexes": [],
                        })
                        table["columns"].append({
                            "name": row["column_name"],
                            "data_type": row["data_type"],
                            "udt_name": row["udt_name"],
                            "nullable": row["is_nullable"] == "YES",
                        })
                    for row in index_rows:
                        key = (row["table_schema"], row["table_name"])
                        if key in tables:
                            tables[key]["indexes"].append({
                                "name": row["index_name"],
                                "definition": row["index_definition"],
                            })

                    inventory = {
                        "table_count": len(tables),
                        "column_count": len(column_rows),
                        "index_count": len(index_rows),
                        "tables": list(tables.values()),
                    }
                    spend = tables.get(("public", "LiteLLM_SpendLogs"))
                    spend_format: dict[str, Any] = {
                        "exists": spend is not None,
                        "selectable": bool(spend and spend.get("selectable")),
                    }
                    if spend and spend.get("selectable"):
                        spend_columns = {
                            column["name"]: column for column in spend.get("columns", [])
                        }
                        missing_required = sorted(
                            set(REQUIRED_SPEND_LOG_COLUMNS) - set(spend_columns)
                        )
                        spend_format["missing_required_columns"] = missing_required
                        spend_format["required_query_compatible"] = False
                        if not missing_required:
                            try:
                                cursor.execute("""
                                    select request_id, call_type, spend, total_tokens, prompt_tokens,
                                           completion_tokens, "startTime", "endTime", model, model_id,
                                           model_group, custom_llm_provider, session_id, status, agent_id,
                                           request_duration_ms, "user", end_user, metadata,
                                           proxy_server_request, messages, response,
                                           metadata->>'user_api_key_alias' as key_alias
                                    from "LiteLLM_SpendLogs" limit 0
                                """)
                                spend_format["required_query_compatible"] = True
                            except Exception as exc:
                                spend_format["required_query_error"] = str(exc)
                                connection.rollback()
                        cursor.execute("""
                            select class.reltuples::bigint as estimated_rows
                            from pg_class as class
                            join pg_namespace as namespace on namespace.oid = class.relnamespace
                            where namespace.nspname = 'public'
                              and class.relname = 'LiteLLM_SpendLogs'
                        """)
                        estimate = cursor.fetchone() or {}
                        spend_format["estimated_rows"] = estimate.get("estimated_rows")
                        sample_columns = {
                            "startTime", "metadata", "proxy_server_request",
                            "messages", "response", "status", "call_type",
                        }
                        json_columns_are_jsonb = all(
                            spend_columns.get(name, {}).get("udt_name") == "jsonb"
                            for name in ("metadata", "proxy_server_request", "messages", "response")
                        )
                        if sample_columns.issubset(spend_columns) and json_columns_are_jsonb:
                            cursor.execute("""
                                with sample as (
                                    select metadata, proxy_server_request, messages, response,
                                           status, call_type, "startTime", session_id, model,
                                           end_user, total_tokens
                                    from "LiteLLM_SpendLogs"
                                    order by "startTime" desc limit 1000
                                )
                                select count(*) as sampled_rows,
                                       count(*) filter (where metadata is not null) as metadata_rows,
                                       count(*) filter (where proxy_server_request is not null) as proxy_rows,
                                       count(*) filter (where messages is not null) as messages_rows,
                                       count(*) filter (where response is not null) as response_rows,
                                       count(*) filter (where session_id is not null and session_id <> '') as session_rows,
                                       count(*) filter (where model is not null and model <> '') as model_rows,
                                       count(*) filter (where end_user is not null and end_user <> '') as end_user_rows,
                                       count(*) filter (where coalesce(total_tokens, 0) > 0) as token_rows,
                                       min("startTime") as oldest_sample_time,
                                       max("startTime") as latest_sample_time
                                from sample
                            """)
                            spend_format["latest_sample"] = dict(cursor.fetchone() or {})
                            cursor.execute("""
                                with sample as (
                                    select metadata, proxy_server_request, messages, response
                                    from "LiteLLM_SpendLogs"
                                    order by "startTime" desc limit 1000
                                ), shapes as (
                                    select 'metadata' as field,
                                           coalesce(jsonb_typeof(metadata), 'sql_null') as json_type
                                    from sample
                                    union all
                                    select 'proxy_server_request',
                                           coalesce(jsonb_typeof(proxy_server_request), 'sql_null')
                                    from sample
                                    union all
                                    select 'messages',
                                           coalesce(jsonb_typeof(messages), 'sql_null')
                                    from sample
                                    union all
                                    select 'response',
                                           coalesce(jsonb_typeof(response), 'sql_null')
                                    from sample
                                )
                                select field, json_type, count(*) as occurrences
                                from shapes group by field, json_type
                                order by field, occurrences desc, json_type
                            """)
                            spend_format["json_shapes"] = list(cursor.fetchall())
                            cursor.execute("""
                                with sample as (
                                    select metadata from "LiteLLM_SpendLogs"
                                    order by "startTime" desc limit 1000
                                ), keys as (
                                    select jsonb_object_keys(metadata) as key
                                    from sample where jsonb_typeof(metadata) = 'object'
                                )
                                select key, count(*) as occurrences from keys
                                group by key order by occurrences desc, key limit 100
                            """)
                            spend_format["metadata_keys"] = list(cursor.fetchall())
                            cursor.execute("""
                                with sample as (
                                    select proxy_server_request from "LiteLLM_SpendLogs"
                                    order by "startTime" desc limit 1000
                                ), objects as (
                                    select proxy_server_request->'metadata' as metadata from sample
                                ), keys as (
                                    select jsonb_object_keys(metadata) as key
                                    from objects where jsonb_typeof(metadata) = 'object'
                                )
                                select key, count(*) as occurrences from keys
                                group by key order by occurrences desc, key limit 100
                            """)
                            spend_format["proxy_metadata_keys"] = list(cursor.fetchall())
                        else:
                            spend_format["sample_shape_status"] = "skipped_incompatible_columns"
                    inventory["spend_log_format"] = spend_format
                    return {"server": server, "inventory": inventory}

        inspected = _database_retry(inspect)
        comparison = compare_database_schema(inspected["inventory"], baseline)
        issues = list(comparison["issues"])
        spend_format = inspected["inventory"].get("spend_log_format") or {}
        if spend_format.get("exists") and spend_format.get("selectable"):
            issues.extend(database_content_issues(spend_format))
            if not spend_format.get("required_query_compatible"):
                issues.append({
                    "severity": "error",
                    "category": "database_required_query_incompatible",
                    "summary": "The evaluator's LiteLLM_SpendLogs query cannot run",
                    "detail": str(
                        spend_format.get("required_query_error")
                        or spend_format.get("missing_required_columns")
                        or "required query failed"
                    ),
                    "suggested_action": "Apply the matching LiteLLM database migration, then rerun the schema audit.",
                    "configuration_changes": [
                        "Align the intranet LiteLLM service and PostgreSQL migration versions.",
                    ],
                })
        status = "ok" if not any(item["severity"] == "error" for item in issues) else "incompatible"
        return _json_safe({
            "status": status,
            "baseline_path": str(selected_baseline),
            "server": inspected["server"],
            "inventory": inspected["inventory"],
            "comparison": comparison,
            "issues": issues,
        })
    except Exception as exc:
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "issues": [{
                "severity": "error",
                "category": "database_schema_audit_failed",
                "summary": "Database schema audit could not complete",
                "detail": str(exc),
                "suggested_action": (
                    "Check DATABASE_URL/DATABASE_* values, SSL mode, network access and "
                    "information_schema/pg_catalog read permissions."
                ),
                "configuration_changes": [
                    "Correct DATABASE_URL or DATABASE_HOST/PORT/NAME/USER/PASSWORD in root .env.",
                ],
            }],
        }
