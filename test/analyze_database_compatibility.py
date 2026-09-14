"""Read-only compatibility audit for the LiteLLM PostgreSQL database.

The report intentionally excludes passwords, API keys, prompts, responses, and
raw metadata values. It answers whether the configured database can supply the
columns and JSON structure required by the schematic conversation overview.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from agent_eval.database import resolve_database_config  # noqa: E402


TABLE_NAME = "LiteLLM_SpendLogs"
OVERVIEW_COLUMNS = {
    "request_id",
    "call_type",
    "user",
    "end_user",
    "startTime",
    "endTime",
    "model",
    "model_group",
    "custom_llm_provider",
    "session_id",
    "status",
    "agent_id",
    "request_duration_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "spend",
    "metadata",
    "proxy_server_request",
}
DETAIL_COLUMNS = OVERVIEW_COLUMNS | {"messages", "response"}
JSON_OPERATOR_COLUMNS = {"metadata", "proxy_server_request"}
METADATA_KEYS = (
    "agent_eval_user_id",
    "agent_eval_task_id",
    "agent_eval_run_id",
    "agent_eval_agent",
    "agent_eval_model",
    "session_id",
    "parent_session_id",
    "session_key",
    "parent_session_key",
    "spawned_by",
    "request_purpose",
)


def _safe_error(exc: Exception) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "sqlstate": getattr(exc, "sqlstate", None),
        "message": str(exc),
    }


def _qualified_table(sql: Any, schema: str) -> Any:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(TABLE_NAME))


def _scalar(cursor: Any, statement: Any, parameters: tuple[Any, ...] = ()) -> Any:
    cursor.execute(statement, parameters)
    row = cursor.fetchone()
    if not row:
        return None
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


def _metadata_coverage(cursor: Any, sql: Any, table: Any, columns: set[str]) -> dict[str, int]:
    compatible_json = {
        name for name in ("metadata", "proxy_server_request") if name in columns
    }
    if not compatible_json:
        return {}
    result: dict[str, int] = {}
    for key in METADATA_KEYS:
        expressions: list[Any] = []
        if "proxy_server_request" in compatible_json:
            expressions.append(
                sql.SQL("proxy_server_request->'metadata'->>{}").format(sql.Literal(key))
            )
        if "metadata" in compatible_json:
            expressions.append(sql.SQL("metadata->>{}").format(sql.Literal(key)))
            expressions.append(
                sql.SQL("metadata->'spend_logs_metadata'->>{}").format(sql.Literal(key))
            )
        statement = sql.SQL("select count(*) from {} where coalesce({}, '') <> ''").format(
            table, sql.SQL(", ").join(expressions)
        )
        result[key] = int(_scalar(cursor, statement) or 0)
    return result


def analyze(*, recent_limit: int) -> tuple[dict[str, Any], bool]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "read_only_litellm_database_compatibility",
        "privacy": "passwords, request content, responses, and raw metadata are excluded",
        "checks": {},
        "warnings": [],
        "recommendations": [],
    }
    try:
        config = resolve_database_config(BACKEND_ROOT)
    except Exception as exc:
        report["checks"]["configuration"] = {"status": "failed", "error": _safe_error(exc)}
        report["compatible_for_schematic_overview"] = False
        return report, False

    report["configuration"] = {
        "enabled": config.enabled,
        "host": config.host,
        "port": config.port,
        "database": config.name,
        "user": config.user,
        "sslmode": config.sslmode,
        "connect_timeout_seconds": config.connect_timeout_seconds,
        "trace_enabled": config.trace_enabled,
        "include_content": config.include_content,
        "password_configured": bool(config.password),
    }
    if not config.enabled:
        report["checks"]["configuration"] = {"status": "disabled"}
        report["compatible_for_schematic_overview"] = False
        return report, False

    try:
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
    except ImportError as exc:
        report["checks"]["driver"] = {"status": "failed", "error": _safe_error(exc)}
        report["compatible_for_schematic_overview"] = False
        return report, False

    try:
        with psycopg.connect(**config.connection_kwargs(), row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """select current_database() as database, current_user as user,
                    current_setting('server_version') as server_version,
                    inet_server_addr()::text as server_address,
                    inet_server_port() as server_port"""
                )
                report["server"] = dict(cursor.fetchone() or {})
                report["checks"]["connection"] = {"status": "ok"}

                cursor.execute(
                    """select table_schema from information_schema.tables
                    where table_name = %s
                    order by case when table_schema = 'public' then 0 else 1 end, table_schema""",
                    (TABLE_NAME,),
                )
                schemas = [str(row["table_schema"]) for row in cursor.fetchall()]
                if not schemas:
                    report["checks"]["table"] = {
                        "status": "failed",
                        "table": TABLE_NAME,
                        "reason": "table_not_found",
                    }
                    report["recommendations"].append(
                        "Confirm that DATABASE_NAME points to the PostgreSQL database used by LiteLLM and run LiteLLM migrations."
                    )
                    report["compatible_for_schematic_overview"] = False
                    return report, False

                schema = schemas[0]
                table = _qualified_table(sql, schema)
                cursor.execute(
                    """select column_name, data_type, udt_name, is_nullable
                    from information_schema.columns
                    where table_schema = %s and table_name = %s
                    order by ordinal_position""",
                    (schema, TABLE_NAME),
                )
                column_rows = [dict(row) for row in cursor.fetchall()]
                column_map = {str(row["column_name"]): row for row in column_rows}
                columns = set(column_map)
                report["table"] = {
                    "schema": schema,
                    "name": TABLE_NAME,
                    "other_matching_schemas": schemas[1:],
                    "columns": column_rows,
                }

                missing_overview = sorted(OVERVIEW_COLUMNS - columns)
                missing_detail = sorted(DETAIL_COLUMNS - columns)
                invalid_json = sorted(
                    name
                    for name in JSON_OPERATOR_COLUMNS & columns
                    if str(column_map[name]["data_type"]) not in {"json", "jsonb"}
                )
                report["compatibility"] = {
                    "missing_overview_columns": missing_overview,
                    "missing_detail_columns": missing_detail,
                    "json_columns_with_incompatible_type": invalid_json,
                }

                privilege = bool(
                    _scalar(
                        cursor,
                        "select has_table_privilege(current_user, %s, 'SELECT')",
                        (f'{schema}."{TABLE_NAME}"',),
                    )
                )
                report["checks"]["table"] = {
                    "status": "ok" if privilege else "failed",
                    "select_privilege": privilege,
                }

                total = int(_scalar(cursor, sql.SQL("select count(*) from {}").format(table)) or 0)
                stats: dict[str, Any] = {"total_rows": total}
                for name in (
                    "session_id", "metadata", "proxy_server_request", "messages",
                    "response", "end_user", "model", "total_tokens",
                ):
                    if name in columns:
                        statement = sql.SQL("select count({}) from {}").format(
                            sql.Identifier(name), table
                        )
                        stats[f"rows_with_{name}"] = int(_scalar(cursor, statement) or 0)
                if "startTime" in columns:
                    stats["oldest_start_time"] = _scalar(
                        cursor,
                        sql.SQL("select min({}) from {}").format(sql.Identifier("startTime"), table),
                    )
                    stats["latest_start_time"] = _scalar(
                        cursor,
                        sql.SQL("select max({}) from {}").format(sql.Identifier("startTime"), table),
                    )
                report["data"] = stats

                metadata_json_ok = not invalid_json and bool(
                    {"metadata", "proxy_server_request"} & columns
                )
                if metadata_json_ok:
                    report["metadata_key_coverage"] = _metadata_coverage(
                        cursor, sql, table, columns
                    )

                safe_recent = [
                    name for name in (
                        "request_id", "startTime", "model", "model_group", "session_id",
                        "end_user", "status", "prompt_tokens", "completion_tokens", "total_tokens",
                    ) if name in columns
                ]
                if safe_recent and total:
                    order = (
                        sql.SQL(" order by {} desc").format(sql.Identifier("startTime"))
                        if "startTime" in columns else sql.SQL("")
                    )
                    statement = sql.SQL("select {} from {}").format(
                        sql.SQL(", ").join(map(sql.Identifier, safe_recent)), table
                    ) + order + sql.SQL(" limit %s")
                    cursor.execute(statement, (recent_limit,))
                    report["recent_rows_without_content"] = [dict(row) for row in cursor.fetchall()]

                overview_ok = privilege and not missing_overview and not invalid_json
                detail_ok = privilege and not missing_detail and not invalid_json
                report["compatible_for_schematic_overview"] = overview_ok
                report["compatible_for_conversation_detail"] = detail_ok
                report["data_available"] = total > 0

                if total == 0:
                    report["warnings"].append(
                        "LiteLLM_SpendLogs exists but contains no rows. Verify that LITELLM_API_BASE writes to this database."
                    )
                if missing_overview:
                    report["recommendations"].append(
                        "Upgrade/migrate LiteLLM or add a version-compatible reader for the missing overview columns."
                    )
                if invalid_json:
                    report["recommendations"].append(
                        "The overview uses PostgreSQL JSON operators; metadata/proxy content columns must be json or jsonb."
                    )
                if not detail_ok and overview_ok:
                    report["warnings"].append(
                        "Conversation cards can be listed, but full input/output details are not compatible."
                    )
                coverage = report.get("metadata_key_coverage") or {}
                if total and not coverage.get("session_id") and not stats.get("rows_with_session_id"):
                    report["warnings"].append(
                        "No session_id evidence was found; calls will be shown as unattributed individual conversations."
                    )
                if total and not coverage.get("agent_eval_task_id"):
                    report["warnings"].append(
                        "No agent_eval_task_id metadata was found; evaluation/non-evaluation classification may be incomplete."
                    )
                if total and not stats.get("rows_with_messages"):
                    report["warnings"].append(
                        "No stored messages were found; the UI cannot reconstruct user/system/history content."
                    )
                if total and not stats.get("rows_with_response"):
                    report["warnings"].append(
                        "No stored responses were found; the UI cannot display model output."
                    )
                return report, overview_ok
    except Exception as exc:
        report["checks"]["database_audit"] = {
            "status": "failed",
            "error": _safe_error(exc),
        }
        report["compatible_for_schematic_overview"] = False
        report["recommendations"].append(
            "Use the SQLSTATE and message above to distinguish network, authentication, permission, and schema errors."
        )
        return report, False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path; parent directories are created automatically.",
    )
    parser.add_argument(
        "--recent-limit",
        type=int,
        default=5,
        help="Number of recent rows to show without prompts, responses, or metadata values (default: 5).",
    )
    args = parser.parse_args()
    if not 0 <= args.recent_limit <= 100:
        parser.error("--recent-limit must be between 0 and 100")

    report, compatible = analyze(recent_limit=args.recent_limit)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Report saved to: {output}", file=sys.stderr)
    return 0 if compatible else 1


if __name__ == "__main__":
    raise SystemExit(main())
