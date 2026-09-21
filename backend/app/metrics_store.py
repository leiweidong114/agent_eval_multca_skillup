from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.schematic_data_client import RATIONALITY_COLLECTION, SchematicDataClient


SESSION_METRICS_CHECK_TYPE = "agent_eval_session_metrics"
SESSION_METRICS_MESSAGE = "Agent Eval 历史会话指标计算结果"


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _metric_document(record: Mapping[str, Any]) -> dict[str, Any] | None:
    if str(record.get("checkType") or "") != SESSION_METRICS_CHECK_TYPE:
        return None
    value = record.get("resultText")
    if isinstance(value, Mapping):
        result = dict(value)
    else:
        try:
            result = json.loads(str(value or ""))
        except (TypeError, ValueError):
            return None
    if not isinstance(result, dict):
        return None
    result["session_id"] = str(result.get("session_id") or record.get("sessionId") or "")
    result["mongo_record_id"] = str(record.get("_id") or "") or None
    result["mongo_uuid"] = str(record.get("uuid") or "") or None
    result["updated_at"] = record.get("createTime") or result.get("calculated_at")
    return result


def _latest_metric(records: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        (record, document)
        for record in records
        for document in [_metric_document(record)]
        if document is not None
    ]
    candidates.sort(
        key=lambda pair: str(pair[0].get("createTime") or pair[1].get("calculated_at") or ""),
        reverse=True,
    )
    return candidates[0][1] if candidates else None


class MetricsStore:
    """Persist and query calculated session metrics through the MongoDB HTTP facade."""

    def __init__(self) -> None:
        self._client = SchematicDataClient()

    def _data_client(self) -> SchematicDataClient:
        client = getattr(self, "_client", None)
        if client is None:
            client = SchematicDataClient()
            self._client = client
        return client

    def build_metrics_record(self, result: dict[str, Any]) -> dict[str, Any]:
        """Build the exact Java/MongoDB payload so callers can audit it before insertion."""
        session_id = str(result["session_id"])
        now = datetime.now(timezone.utc)
        document = {**result, "session_id": session_id, "updated_at": now}
        return {
            "uuid": uuid.uuid4().hex,
            "status": "completed",
            "createUser": str(result.get("end_user") or "agent-eval"),
            "createTime": now.isoformat(),
            "checkType": SESSION_METRICS_CHECK_TYPE,
            "checkMessage": SESSION_METRICS_MESSAGE,
            "userName": str(result.get("end_user") or "Agent Eval"),
            "hscopeProjectId": str(result.get("task_type") or "session-metrics"),
            "boardNum": str(result.get("metric_definition_version") or ""),
            "sessionId": session_id,
            "resultText": json.dumps(document, ensure_ascii=False, default=_json_default),
        }

    def upsert_metrics(
        self,
        result: dict[str, Any],
        *,
        record: Mapping[str, Any] | None = None,
    ) -> Any:
        record = dict(record) if record is not None else self.build_metrics_record(result)
        client = self._data_client()
        if hasattr(client, "clear_diagnostics"):
            client.clear_diagnostics()
        return client.insert_record(record, collection_name=RATIONALITY_COLLECTION)

    def query_diagnostics(self) -> list[dict[str, Any]]:
        client = self._data_client()
        return client.query_diagnostics() if hasattr(client, "query_diagnostics") else []

    def write_diagnostics(self) -> list[dict[str, Any]]:
        client = self._data_client()
        return client.write_diagnostics() if hasattr(client, "write_diagnostics") else []

    def query_endpoint(self) -> str:
        return self._data_client().query_url

    def write_endpoint(self) -> str:
        return self._data_client().write_url

    def _records_for(self, session_ids: Iterable[str]) -> list[dict[str, Any]]:
        return self._data_client().find_records(RATIONALITY_COLLECTION, session_ids)

    def statuses(self, session_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(str(value) for value in session_ids if str(value)))
        result: dict[str, dict[str, Any]] = {}
        for session_id in ids:
            metric = _latest_metric(self._records_for([session_id]))
            if metric:
                result[session_id] = {
                    "_id": session_id,
                    "status": metric.get("status"),
                    "calculated_at": metric.get("calculated_at"),
                    "metric_definition_version": metric.get("metric_definition_version"),
                    "task_type": metric.get("task_type"),
                    "task_category": metric.get("task_category"),
                    "task_subtype": metric.get("task_subtype"),
                }
        return result

    def _all_latest(self) -> list[dict[str, Any]]:
        latest: dict[str, tuple[str, dict[str, Any]]] = {}
        for record in self._data_client().iter_collection(RATIONALITY_COLLECTION):
            metric = _metric_document(record)
            if not metric or not metric.get("session_id"):
                continue
            session_id = str(metric["session_id"])
            stamp = str(record.get("createTime") or metric.get("calculated_at") or "")
            if session_id not in latest or stamp > latest[session_id][0]:
                latest[session_id] = (stamp, metric)
        return [value[1] for value in latest.values()]

    def session_ids_for_task_classification(self, value: str) -> set[str]:
        field = "task_category" if value == "schematic_generation" else "task_type"
        return {
            str(item["session_id"])
            for item in self._all_latest()
            if item.get(field) == value and item.get("session_id")
        }

    def save_job(self, job: dict[str, Any]) -> None:
        # Progress is intentionally process-local. Only completed metrics are persisted.
        del job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        del job_id
        return None

    def list_metrics(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        task_type: str | None = None,
        agent: str | None = None,
        model: str | None = None,
        end_user: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        values: list[dict[str, Any]] = []
        for item in self._all_latest():
            finished = _parse_time(item.get("finished_at"))
            if finished is None or not (start_time <= finished < end_time):
                continue
            filters = {"task_type": task_type, "agent": agent, "model": model, "end_user": end_user}
            if any(
                expected and str(item.get(name) or "") != expected
                for name, expected in filters.items()
            ):
                continue
            values.append(item)
        values.sort(key=lambda item: str(item.get("finished_at") or ""), reverse=True)
        return {
            "items": values[offset:offset + limit],
            "total": len(values),
            "limit": limit,
            "offset": offset,
        }

    def get_metrics(self, session_id: str) -> dict[str, Any] | None:
        return _latest_metric(self._records_for([session_id]))

    def latest_rationality_analysis(
        self,
        session_id: str,
        *,
        correlation_ids: Iterable[str] = (),
    ) -> tuple[dict[str, Any] | None, int, str | None]:
        identifiers = list(dict.fromkeys(
            value for value in [str(session_id), *(str(item) for item in correlation_ids)] if value
        ))
        if hasattr(self._data_client(), "clear_diagnostics"):
            self._data_client().clear_diagnostics()
        values = [
            value
            for value in self._data_client().find_rationality_records(identifiers)
            if str(value.get("checkType") or "") != SESSION_METRICS_CHECK_TYPE
        ]
        completed = [
            value
            for value in values
            if str(value.get("status") or "").lower() in {"completed", "success", "ok"}
        ]
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
            group = groups.setdefault(task_type, {
                "task_type": task_type,
                "sessions": 0,
                "average_tool_success_rate": None,
                "average_skill_completeness": None,
                "error_count": 0,
                "retry_count": 0,
                "suspected_fabrication_count": 0,
            })
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
            for target, source in (
                ("average_tool_success_rate", "tool"),
                ("average_skill_completeness", "skill"),
            ):
                series = rates[task_type][source]
                group[target] = sum(series) / len(series) if series else None
        values = sorted(groups.values(), key=lambda item: item["sessions"], reverse=True)
        return {
            "total_sessions": result["total"],
            "groups": values,
            "start_time": start_time,
            "end_time": end_time,
        }


def metrics_store_health() -> dict[str, Any]:
    try:
        client = SchematicDataClient()
        client.query_page(
            collection_name=RATIONALITY_COLLECTION,
            page=1,
            size=1,
            use_cache=False,
        )
        return {
            "status": "ok",
            "backend": "mongodb_via_http",
            "collection": RATIONALITY_COLLECTION,
            "check_type": SESSION_METRICS_CHECK_TYPE,
        }
    except Exception as exc:
        return {"status": "unavailable", "backend": "mongodb_via_http", "detail": str(exc)}
