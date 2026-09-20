from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.infrastructure_config import load_infrastructure_settings
from app.schematic_data_client import SchematicDataClient


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _document(value: str) -> dict[str, Any]:
    result = json.loads(value)
    return result if isinstance(result, dict) else {}


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


class MetricsStore:
    """Local metrics persistence plus HTTP access to schematic analysis data."""

    _INIT_LOCK = threading.Lock()

    def __init__(self) -> None:
        settings = load_infrastructure_settings()
        self._path = Path(settings.metrics_sqlite_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path, timeout=10, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._INIT_LOCK:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=10000;
                CREATE TABLE IF NOT EXISTS session_metrics_latest (
                    session_id TEXT PRIMARY KEY,
                    status TEXT, calculated_at TEXT, finished_at TEXT,
                    task_type TEXT, task_category TEXT, task_subtype TEXT,
                    agent TEXT, model TEXT, end_user TEXT,
                    metric_definition_version TEXT, document_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_metrics_finished ON session_metrics_latest(finished_at DESC);
                CREATE INDEX IF NOT EXISTS idx_metrics_task ON session_metrics_latest(task_type, finished_at DESC);
                CREATE TABLE IF NOT EXISTS session_metrics_versions (
                    session_id TEXT NOT NULL, metric_definition_version TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL, document_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, metric_definition_version, source_fingerprint)
                );
                CREATE TABLE IF NOT EXISTS metric_calculation_jobs (
                    job_id TEXT PRIMARY KEY, document_json TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row[1])
                for row in self._connection.execute("PRAGMA table_info(session_metrics_latest)").fetchall()
            }
            for name in ("task_category", "task_subtype"):
                if name not in columns:
                    self._connection.execute(
                        f"ALTER TABLE session_metrics_latest ADD COLUMN {name} TEXT"
                    )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_metrics_category ON session_metrics_latest(task_category, task_type, finished_at DESC)"
            )
            self._connection.commit()

    def upsert_metrics(self, result: dict[str, Any]) -> None:
        session_id = str(result["session_id"])
        document = {**result, "_id": session_id, "updated_at": datetime.now(timezone.utc)}
        encoded = json.dumps(document, ensure_ascii=False, default=_json_default)
        values = (
            session_id, str(result.get("status") or ""), _timestamp(result.get("calculated_at")),
            _timestamp(result.get("finished_at")), str(result.get("task_type") or ""),
            str(result.get("task_category") or ""), str(result.get("task_subtype") or ""),
            str(result.get("agent") or ""), str(result.get("model") or ""),
            str(result.get("end_user") or ""), str(result.get("metric_definition_version") or ""),
            encoded,
        )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO session_metrics_latest(
                    session_id,status,calculated_at,finished_at,task_type,task_category,task_subtype,agent,model,end_user,
                    metric_definition_version,document_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status=excluded.status, calculated_at=excluded.calculated_at,
                    finished_at=excluded.finished_at, task_type=excluded.task_type,
                    task_category=excluded.task_category, task_subtype=excluded.task_subtype,
                    agent=excluded.agent, model=excluded.model, end_user=excluded.end_user,
                    metric_definition_version=excluded.metric_definition_version,
                    document_json=excluded.document_json
                """, values,
            )
            self._connection.execute(
                """
                INSERT INTO session_metrics_versions(
                    session_id,metric_definition_version,source_fingerprint,document_json
                ) VALUES(?,?,?,?)
                ON CONFLICT(session_id,metric_definition_version,source_fingerprint)
                DO UPDATE SET document_json=excluded.document_json
                """,
                (session_id, str(result.get("metric_definition_version") or ""),
                 str(result.get("source_fingerprint") or ""), encoded),
            )

    def statuses(self, session_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(str(value) for value in session_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._connection.execute(
            f"SELECT session_id,status,calculated_at,metric_definition_version,task_type,task_category,task_subtype FROM session_metrics_latest WHERE session_id IN ({placeholders})",
            ids,
        ).fetchall()
        return {str(row["session_id"]): {
            "_id": row["session_id"], "status": row["status"],
            "calculated_at": row["calculated_at"],
            "metric_definition_version": row["metric_definition_version"],
            "task_type": row["task_type"],
            "task_category": row["task_category"],
            "task_subtype": row["task_subtype"],
        } for row in rows}

    def session_ids_for_task_classification(self, value: str) -> set[str]:
        if value == "schematic_generation":
            rows = self._connection.execute(
                "SELECT session_id FROM session_metrics_latest WHERE task_category=?",
                (value,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT session_id FROM session_metrics_latest WHERE task_type=?",
                (value,),
            ).fetchall()
        return {str(row["session_id"]) for row in rows}

    def save_job(self, job: dict[str, Any]) -> None:
        encoded = json.dumps(job, ensure_ascii=False, default=_json_default)
        with self._connection:
            self._connection.execute(
                "INSERT INTO metric_calculation_jobs(job_id,document_json) VALUES(?,?) ON CONFLICT(job_id) DO UPDATE SET document_json=excluded.document_json",
                (str(job["job_id"]), encoded),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT document_json FROM metric_calculation_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return _document(row["document_json"]) if row else None

    def list_metrics(self, *, start_time: datetime, end_time: datetime,
                     task_type: str | None = None, agent: str | None = None,
                     model: str | None = None, end_user: str | None = None,
                     limit: int = 20, offset: int = 0) -> dict[str, Any]:
        clauses = ["finished_at >= ?", "finished_at < ?"]
        values: list[Any] = [_timestamp(start_time), _timestamp(end_time)]
        for name, value in {"task_type": task_type, "agent": agent, "model": model, "end_user": end_user}.items():
            if value:
                clauses.append(f"{name} = ?")
                values.append(value)
        where = " AND ".join(clauses)
        total = int(self._connection.execute(
            f"SELECT COUNT(*) FROM session_metrics_latest WHERE {where}", values
        ).fetchone()[0])
        rows = self._connection.execute(
            f"SELECT document_json FROM session_metrics_latest WHERE {where} ORDER BY finished_at DESC LIMIT ? OFFSET ?",
            [*values, limit, offset],
        ).fetchall()
        items = []
        for row in rows:
            value = _document(row["document_json"])
            value["session_id"] = str(value.pop("_id", value.get("session_id") or ""))
            items.append(value)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_metrics(self, session_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT document_json FROM session_metrics_latest WHERE session_id=?", (session_id,)
        ).fetchone()
        if not row:
            return None
        value = _document(row["document_json"])
        value["session_id"] = str(value.pop("_id", session_id))
        return value

    def latest_rationality_analysis(self, session_id: str, *, correlation_ids: Iterable[str] = ()) -> tuple[dict[str, Any] | None, int, str | None]:
        identifiers = list(dict.fromkeys(
            value for value in [str(session_id), *(str(item) for item in correlation_ids)] if value
        ))
        values = SchematicDataClient().find_rationality_records(identifiers)
        completed = [value for value in values if str(value.get("status") or "").lower() in {"completed", "success", "ok"}]
        completed.sort(key=lambda value: str(value.get("createTime") or ""), reverse=True)
        result = dict(completed[0]) if completed else None
        if result is not None:
            result["_id"] = str(result.get("_id") or "")
        matched_by = None
        if result is not None:
            matched_by = "root_session_id" if result.get("sessionId") == session_id else "evaluation_run_id"
        return result, len(values), matched_by

    def summary(self, *, start_time: datetime, end_time: datetime) -> dict[str, Any]:
        result = self.list_metrics(start_time=start_time, end_time=end_time, limit=100000)
        groups: dict[str, dict[str, Any]] = {}
        rates: dict[str, dict[str, list[float]]] = {}
        for item in result["items"]:
            task_type = str(item.get("task_type") or "other")
            group = groups.setdefault(task_type, {"task_type": task_type, "sessions": 0,
                "average_tool_success_rate": None, "average_skill_completeness": None,
                "error_count": 0, "retry_count": 0, "suspected_fabrication_count": 0})
            series = rates.setdefault(task_type, {"tool": [], "skill": []})
            metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
            group["sessions"] += 1
            group["error_count"] += int(metrics.get("error_count") or 0)
            group["retry_count"] += int(metrics.get("retry_attempt_count") or 0)
            group["suspected_fabrication_count"] += int(metrics.get("suspected_fabrication_count") or 0)
            if isinstance(metrics.get("tool_success_rate"), (int, float)):
                series["tool"].append(float(metrics["tool_success_rate"]))
            if isinstance(metrics.get("skill_completeness"), (int, float)):
                series["skill"].append(float(metrics["skill_completeness"]))
        for task_type, group in groups.items():
            for target, source in (("average_tool_success_rate", "tool"), ("average_skill_completeness", "skill")):
                series = rates[task_type][source]
                group[target] = sum(series) / len(series) if series else None
        values = sorted(groups.values(), key=lambda item: item["sessions"], reverse=True)
        return {"total_sessions": result["total"], "groups": values,
                "start_time": start_time, "end_time": end_time}


def metrics_store_health() -> dict[str, Any]:
    try:
        store = MetricsStore()
        store._connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "backend": "sqlite", "path": str(store._path)}
    except Exception as exc:
        return {"status": "unavailable", "detail": str(exc)}
