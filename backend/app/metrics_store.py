from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.schematic_data_client import RATIONALITY_COLLECTION, SchematicDataClient
from app.quality_summary import compact_agent_eval_metrics, flatten_agent_eval_metrics
from app.quality_aggregate import AGGREGATE_CHECK_TYPE, AGGREGATE_SESSION_ID, aggregate_quality_metrics


SESSION_METRICS_CHECK_TYPE = "agent_eval_session_metrics"
SESSION_METRICS_MESSAGE = "Agent Eval 历史会话指标计算结果"
SESSION_PROCESS_CHECK_TYPE = "agent_eval_metric_process"
SESSION_PROCESS_MESSAGE = "Agent Eval 历史会话指标计算过程"
_aggregate_lock = threading.RLock()


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
    if str(record.get("checkType") or "").strip() == AGGREGATE_CHECK_TYPE:
        return None
    if str(record.get("checkType") or "").strip() == SESSION_METRICS_CHECK_TYPE:
        value = record.get("resultText")
        try:
            result = dict(value) if isinstance(value, Mapping) else json.loads(str(value or ""))
        except (TypeError, ValueError):
            return None
        if not isinstance(result, dict):
            return None
        if isinstance(record.get("agentEvalMetrics"), Mapping):
            result["agentEvalMetrics"] = dict(record["agentEvalMetrics"])
            if not isinstance(result.get("quality_summary"), Mapping):
                result["quality_summary"] = dict(record["agentEvalMetrics"])
        result["session_id"] = str(result.get("session_id") or record.get("sessionId") or "")
        result["mongo_record_id"] = str(record.get("_id") or "") or None
        result["mongo_uuid"] = str(record.get("uuid") or "") or None
        result["updated_at"] = record.get("createTime") or result.get("calculated_at")
        return result
    embedded = record.get("agentEvalMetrics")
    if isinstance(embedded, Mapping):
        result = dict(embedded)
        result["session_id"] = str(result.get("session_id") or record.get("sessionId") or "")
        result["mongo_record_id"] = str(record.get("_id") or "") or None
        result["mongo_uuid"] = str(record.get("uuid") or "") or None
        return result
    return None


def _latest_metric(records: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        (record, document)
        for record in records
        for document in [_metric_document(record)]
        if document is not None
    ]
    candidates.sort(
        key=lambda pair: str(pair[1].get("updated_at") or pair[0].get("createTime") or pair[1].get("calculated_at") or ""),
        reverse=True,
    )
    return candidates[0][1] if candidates else None


def _bounded_process_value(value: Any, depth: int = 0) -> Any:
    """Keep a retrievable trace below MongoDB's document limit without secrets in headers."""
    if isinstance(value, str):
        return value[:4000] + ("…[已截断]" if len(value) > 4000 else "")
    if isinstance(value, datetime):
        return value.isoformat()
    if depth >= 8:
        return "[嵌套内容已截断]"
    if isinstance(value, Mapping):
        return {str(key): _bounded_process_value(item, depth + 1) for key, item in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [_bounded_process_value(item, depth + 1) for item in value[:100]]
    return value


def _bounded_process_event(event: Mapping[str, Any]) -> dict[str, Any]:
    bounded = _bounded_process_value(event)
    if len(json.dumps(bounded, ensure_ascii=False, default=_json_default).encode("utf-8")) <= 8192:
        return bounded
    return {
        key: _bounded_process_value(event[key])
        for key in ("sequence", "timestamp", "stage", "message", "session_id", "outcome", "detail")
        if key in event
    } | {"event_detail_truncated": True}


class MetricsStore:
    """Persist and query calculated session metrics through the MongoDB HTTP facade."""

    def __init__(self) -> None:
        self._client = SchematicDataClient()
        self._latest_rationality_diagnostic: dict[str, Any] = {}

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
        record = {
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
        if isinstance(result.get("quality_summary"), Mapping):
            record["agentEvalMetrics"] = compact_agent_eval_metrics(result["quality_summary"])
        return record

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

    def verify_metric_persisted(
        self,
        session_id: str,
        expected_uuid: str,
    ) -> dict[str, Any]:
        """Read a just-written metric back from MongoDB through the Java facade."""
        client = self._data_client()
        try:
            records = client.find_records(
                RATIONALITY_COLLECTION,
                [session_id],
                use_cache=False,
            )
        except TypeError:
            # Small fake clients used by integrations may implement the older
            # two-argument protocol. Production SchematicDataClient always
            # takes use_cache and bypasses TTL/LRU here.
            records = client.find_records(RATIONALITY_COLLECTION, [session_id])
        metric_records = [
            record
            for record in records
            if str(record.get("checkType") or "") == SESSION_METRICS_CHECK_TYPE
        ]
        matching = next(
            (
                record
                for record in metric_records
                if str(record.get("uuid") or "") == str(expected_uuid)
            ),
            None,
        )
        if matching is None:
            matching = next((item for item in records
                             if isinstance(item.get("agentEvalMetrics"), Mapping)
                             and str(item["agentEvalMetrics"].get("uuid") or "") == str(expected_uuid)), None)
        parsed = _metric_document(matching) if matching is not None else None
        verified = matching is not None and parsed is not None
        reason = None
        if matching is None:
            reason = "写入接口返回成功，但按相同 Session ID 回读时没有找到本次 UUID"
        elif parsed is None:
            reason = "已回读到本次 UUID，但 resultText 不是可解析的指标 JSON"
        return {
            "verified": verified,
            "reason": reason,
            "session_id": session_id,
            "expected_uuid": expected_uuid,
            "records_returned": len(records),
            "metric_records_returned": len(metric_records),
            "returned_metric_uuids": [str(item.get("uuid") or "") for item in metric_records[:50]],
            "record_id": str((matching or {}).get("_id") or "") or None,
            "record_status": (matching or {}).get("status"),
            "parsed_metric_status": (parsed or {}).get("status"),
        }

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

    def update_endpoint(self) -> str:
        return self._data_client().update_url

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
                    "quality_rates": flatten_agent_eval_metrics(metric.get("agentEvalMetrics") or metric.get("quality_summary") or {}),
                }
        return result

    def all_statuses(self) -> dict[str, dict[str, Any]]:
        """Return the latest persisted metric status keyed by root Session ID."""
        result: dict[str, dict[str, Any]] = {}
        for metric in self._all_latest():
            session_id = str(metric.get("session_id") or "")
            if not session_id:
                continue
            result[session_id] = {
                "_id": session_id,
                "status": metric.get("status"),
                "calculated_at": metric.get("calculated_at"),
                "metric_definition_version": metric.get("metric_definition_version"),
                "task_type": metric.get("task_type"),
                "task_category": metric.get("task_category"),
                "task_subtype": metric.get("task_subtype"),
                "quality_rates": flatten_agent_eval_metrics(metric.get("agentEvalMetrics") or metric.get("quality_summary") or {}),
            }
        return result

    def _all_latest(self) -> list[dict[str, Any]]:
        latest: dict[str, tuple[str, dict[str, Any]]] = {}
        for record in self._data_client().iter_collection(RATIONALITY_COLLECTION):
            metric = _metric_document(record)
            if not metric or not metric.get("session_id"):
                continue
            session_id = str(metric["session_id"])
            stamp = str(metric.get("updated_at") or record.get("createTime") or metric.get("calculated_at") or "")
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

    def quality_records(self, session_id: str) -> list[dict[str, Any]]:
        """Fetch every original quality report for one exact Session ID."""
        from app.quality_summary import QUALITY_TYPES

        client = self._data_client()
        try:
            rows = client.find_records(RATIONALITY_COLLECTION, [session_id], use_cache=False)
        except TypeError:
            rows = client.find_records(RATIONALITY_COLLECTION, [session_id])
        return [row for row in rows if str(row.get("checkType") or "").strip() in QUALITY_TYPES]

    def get_quality_aggregate(self) -> dict[str, Any] | None:
        try:
            records = self._data_client().find_records(RATIONALITY_COLLECTION, [AGGREGATE_SESSION_ID], use_cache=False)
        except TypeError:
            records = self._data_client().find_records(RATIONALITY_COLLECTION, [AGGREGATE_SESSION_ID])
        candidates = [row for row in records if str(row.get("checkType") or "").strip() == AGGREGATE_CHECK_TYPE]
        candidates.sort(key=lambda row: (str(row.get("createTime") or ""), str(row.get("_id") or "")), reverse=True)
        for row in candidates:
            try:
                value = json.loads(str(row.get("resultText") or ""))
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                return {**value, "mongo_record_id": str(row.get("_id") or "") or None}
        return None

    def refresh_quality_aggregate(self) -> dict[str, Any]:
        """Rebuild after each successful session write, then verify MongoDB readback."""
        with _aggregate_lock:
            client = self._data_client()
            if hasattr(client, "query_page"):
                rows: list[dict[str, Any]] = []
                page = 1
                page_size = 100
                while True:
                    page_rows, total = client.query_page(
                        collection_name=RATIONALITY_COLLECTION, page=page,
                        size=page_size, use_cache=False)
                    rows.extend(page_rows)
                    if total is not None and len(rows) >= total:
                        break
                    if not page_rows:
                        if total is not None and len(rows) < total:
                            raise RuntimeError(f"MongoDB 分页不完整：读取 {len(rows)}/{total} 条，未写入累计指标")
                        break
                    if len(page_rows) < page_size:
                        if total is not None and len(rows) < total:
                            raise RuntimeError(f"MongoDB 分页提前结束：读取 {len(rows)}/{total} 条，未写入累计指标")
                        break
                    page += 1
                    if page > 10000:
                        raise RuntimeError("MongoDB 分页超过 10000 页，未写入可能不完整的累计指标")
            else:
                rows = client.iter_collection(RATIONALITY_COLLECTION)
            latest: dict[str, tuple[tuple[str, str], dict[str, Any]]] = {}
            for row in rows:
                if str(row.get("checkType") or "").strip() != SESSION_METRICS_CHECK_TYPE:
                    continue
                metric = _metric_document(row)
                if not metric or not metric.get("session_id"):
                    continue
                session_id = str(metric["session_id"])
                stamp = (str(row.get("createTime") or metric.get("updated_at") or ""),
                         str(row.get("_id") or ""))
                if session_id not in latest or stamp > latest[session_id][0]:
                    latest[session_id] = (stamp, metric)
            rollup = aggregate_quality_metrics([value[1] for value in latest.values()])
            previous = self.get_quality_aggregate()
            if (previous is not None and previous.get("rates") == rollup["rates"]
                    and previous.get("counts") == rollup["counts"]
                    and previous.get("metric_session_counts") == rollup["metric_session_counts"]
                    and previous.get("rate_only_session_counts") == rollup["rate_only_session_counts"]):
                return previous
            now = datetime.now(timezone.utc).isoformat()
            aggregate = {**rollup, "updated_at": now, "status": "completed"}
            record = {
                "uuid": uuid.uuid4().hex, "status": "completed", "createUser": "agent-eval",
                "createTime": now, "checkType": AGGREGATE_CHECK_TYPE,
                "checkMessage": "Agent Eval 全部已计算会话累计质量指标",
                "userName": "Agent Eval", "hscopeProjectId": "quality-aggregate",
                "boardNum": "aggregate-v1", "sessionId": AGGREGATE_SESSION_ID,
                "agentEvalMetrics": rollup["rates"],
                "resultText": json.dumps(aggregate, ensure_ascii=False, default=_json_default),
            }
            client.insert_record(record, collection_name=RATIONALITY_COLLECTION)
            saved = self.get_quality_aggregate()
            if saved is None or saved.get("updated_at") != now or saved.get("rates") != rollup["rates"]:
                raise RuntimeError("累计质量指标写入后回读不一致")
            return saved

    def save_process_trace(self, job: Mapping[str, Any], session_id: str) -> dict[str, Any]:
        """Persist one session's completed pipeline alongside its metric record."""
        events = [
            _bounded_process_event(event)
            for event in job.get("events") or []
            if isinstance(event, Mapping) and event.get("session_id") == session_id
        ]
        original_event_count = len(events)
        if len(events) > 200:
            events = [*events[:20], {"stage": "events_truncated", "message": "中间过程事件已截断"}, *events[-179:]]
        finished_at = datetime.now(timezone.utc)
        failed = any(event.get("stage") == "session_failed" for event in events)
        document = {
            "job_id": str(job.get("job_id") or ""),
            "session_id": session_id,
            "status": "failed" if failed else "completed",
            "user_id": str(job.get("user_id") or ""),
            "use_llm_judge": bool(job.get("use_llm_judge")),
            "created_at": job.get("created_at"),
            "finished_at": finished_at,
            "event_count": len(events),
            "original_event_count": original_event_count,
            "events_truncated": original_event_count > len(events),
            "events": events,
        }
        record = {
            "uuid": uuid.uuid4().hex,
            "status": document["status"],
            "createUser": document["user_id"] or "agent-eval",
            "createTime": finished_at.isoformat(),
            "checkType": SESSION_PROCESS_CHECK_TYPE,
            "checkMessage": SESSION_PROCESS_MESSAGE,
            "userName": document["user_id"] or "Agent Eval",
            "hscopeProjectId": "session-metrics-process",
            "boardNum": str(job.get("job_id") or ""),
            "sessionId": session_id,
            "resultText": json.dumps(document, ensure_ascii=False, default=_json_default),
        }
        client = self._data_client()
        client.insert_record(record, collection_name=RATIONALITY_COLLECTION)
        try:
            read_back = client.find_records(RATIONALITY_COLLECTION, [session_id], use_cache=False)
        except TypeError:
            read_back = client.find_records(RATIONALITY_COLLECTION, [session_id])
        if not any(
            item.get("checkType") == SESSION_PROCESS_CHECK_TYPE and item.get("uuid") == record["uuid"]
            for item in read_back
        ):
            raise RuntimeError("计算过程写入接口返回成功，但 MongoDB 回读未找到本次过程记录")
        return document

    def get_process_trace(self, session_id: str) -> dict[str, Any] | None:
        all_records = self._records_for([session_id])
        embedded = [(record, record.get("agentEvalProcess")) for record in all_records
                    if isinstance(record.get("agentEvalProcess"), Mapping)]
        embedded.sort(key=lambda pair: str(pair[1].get("finished_at") or ""), reverse=True)
        if embedded:
            record, value = embedded[0]
            return {**value, "mongo_record_id": str(record.get("_id") or "") or None}
        records = [
            item for item in all_records
            if item.get("checkType") == SESSION_PROCESS_CHECK_TYPE
        ]
        records.sort(key=lambda item: str(item.get("createTime") or ""), reverse=True)
        for record in records:
            try:
                value = json.loads(str(record.get("resultText") or ""))
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value.get("session_id") == session_id:
                return {**value, "mongo_record_id": str(record.get("_id") or "") or None}
        return None

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
        client = self._data_client()
        try:
            all_values = client.find_rationality_records(identifiers, use_cache=False)
        except TypeError:
            all_values = client.find_rationality_records(identifiers)
        self_metrics = [
            value
            for value in all_values
            if str(value.get("checkType") or "") == SESSION_METRICS_CHECK_TYPE
        ]
        self_processes = [
            value
            for value in all_values
            if str(value.get("checkType") or "") == SESSION_PROCESS_CHECK_TYPE
        ]
        values = [
            value
            for value in all_values
            if str(value.get("checkType") or "") not in {SESSION_METRICS_CHECK_TYPE, SESSION_PROCESS_CHECK_TYPE, AGGREGATE_CHECK_TYPE}
        ]
        # status is supplied by the upstream analysis service and is not a
        # prerequisite for reading its resultText. Session ID is the join key.
        values.sort(key=lambda value: str(value.get("createTime") or ""), reverse=True)
        result = dict(values[0]) if values else None
        if result is not None:
            result["_id"] = str(result.get("_id") or "")
        matched_by = None
        if result is not None:
            matched_by = "root_session_id" if result.get("sessionId") == session_id else "evaluation_run_id"
        status_counts: dict[str, int] = {}
        check_type_counts: dict[str, int] = {}
        for value in all_values:
            status = str(value.get("status") or "<empty>")
            check_type = str(value.get("checkType") or "<empty>")
            status_counts[status] = status_counts.get(status, 0) + 1
            check_type_counts[check_type] = check_type_counts.get(check_type, 0) + 1
        if not all_values:
            reason = "Java 查询成功，但没有返回任何 Session ID 精确匹配记录"
        elif not values:
            reason = "只找到平台自身的指标或计算过程记录，未找到原理图轨迹质量记录"
        else:
            reason = None
        self._latest_rationality_diagnostic = {
            "identifiers": identifiers,
            "exact_match_record_count": len(all_values),
            "rationality_record_count": len(values),
            "self_metric_record_count": len(self_metrics),
            "self_process_record_count": len(self_processes),
            "eligible_record_count": len(values),
            "status_filter": "none",
            "excluded_check_types": [SESSION_METRICS_CHECK_TYPE, SESSION_PROCESS_CHECK_TYPE],
            "status_counts": status_counts,
            "check_type_counts": check_type_counts,
            "returned_session_ids": sorted({str(value.get("sessionId") or "") for value in all_values}),
            "record_samples": [
                {
                    "_id": str(value.get("_id") or "") or None,
                    "uuid": value.get("uuid"),
                    "sessionId": value.get("sessionId"),
                    "status": value.get("status"),
                    "checkType": value.get("checkType"),
                    "createTime": value.get("createTime"),
                }
                for value in all_values[:20]
            ],
            "reason": reason,
        }
        return result, len(values), matched_by

    def latest_rationality_diagnostic(self) -> dict[str, Any]:
        return dict(getattr(self, "_latest_rationality_diagnostic", {}))

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
