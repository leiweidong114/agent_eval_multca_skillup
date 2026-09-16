from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.infrastructure_config import InfrastructureConfigurationError, load_infrastructure_settings


class MetricsStore:
    """MongoDB persistence for versioned historical-session metrics."""

    def __init__(self) -> None:
        settings = load_infrastructure_settings()
        if not settings.mongodb_uri:
            raise InfrastructureConfigurationError("MongoDB指标库尚未配置")
        try:
            from pymongo import ASCENDING, DESCENDING, MongoClient
        except ImportError as exc:
            raise InfrastructureConfigurationError("缺少pymongo依赖，请安装metrics依赖") from exc
        self._client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
        self._database = self._client[settings.mongodb_database]
        self._latest = self._database["session_metrics_latest"]
        self._versions = self._database["session_metrics_versions"]
        self._jobs = self._database["metric_calculation_jobs"]
        self._rationality = self._database["HDschematicRationalilyCollection"]
        self._client.admin.command("ping")
        self._latest.create_index([("calculated_at", DESCENDING)])
        self._latest.create_index([("task_type", ASCENDING), ("finished_at", DESCENDING)])
        self._latest.create_index([("agent", ASCENDING), ("finished_at", DESCENDING)])
        self._latest.create_index([("model", ASCENDING), ("finished_at", DESCENDING)])
        self._latest.create_index([("end_user", ASCENDING), ("finished_at", DESCENDING)])
        self._versions.create_index(
            [("session_id", ASCENDING), ("metric_definition_version", ASCENDING), ("source_fingerprint", ASCENDING)],
            unique=True,
        )
        self._rationality.create_index([("uuid", ASCENDING)], unique=True)
        self._rationality.create_index([("sessionId", ASCENDING), ("createTime", DESCENDING)])

    def upsert_metrics(self, result: dict[str, Any]) -> None:
        session_id = str(result["session_id"])
        document = {**result, "_id": session_id, "updated_at": datetime.now(timezone.utc)}
        for name in ("started_at", "finished_at", "calculated_at"):
            value = document.get(name)
            if isinstance(value, str):
                try:
                    document[name] = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    pass
        self._latest.replace_one({"_id": session_id}, document, upsert=True)
        version_document = {key: value for key, value in document.items() if key != "_id"}
        self._versions.update_one(
            {
                "session_id": session_id,
                "metric_definition_version": result["metric_definition_version"],
                "source_fingerprint": result["source_fingerprint"],
            },
            {"$set": version_document},
            upsert=True,
        )

    def statuses(self, session_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(str(value) for value in session_ids))
        if not ids:
            return {}
        projection = {"_id": 1, "status": 1, "calculated_at": 1, "metric_definition_version": 1}
        return {str(row["_id"]): row for row in self._latest.find({"_id": {"$in": ids}}, projection)}

    def save_job(self, job: dict[str, Any]) -> None:
        self._jobs.replace_one({"_id": job["job_id"]}, {**job, "_id": job["job_id"]}, upsert=True)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        value = self._jobs.find_one({"_id": job_id})
        if value is not None:
            value.pop("_id", None)
        return value

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
        query: dict[str, Any] = {"finished_at": {"$gte": start_time, "$lt": end_time}}
        for name, value in {
            "task_type": task_type,
            "agent": agent,
            "model": model,
            "end_user": end_user,
        }.items():
            if value:
                query[name] = value
        total = self._latest.count_documents(query)
        cursor = self._latest.find(query).sort("finished_at", -1).skip(offset).limit(limit)
        items = []
        for row in cursor:
            row["session_id"] = str(row.pop("_id", row.get("session_id") or ""))
            items.append(row)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_metrics(self, session_id: str) -> dict[str, Any] | None:
        value = self._latest.find_one({"_id": session_id})
        if value is not None:
            value["session_id"] = str(value.pop("_id"))
        return value

    def latest_rationality_analysis(
        self, session_id: str, *, correlation_ids: Iterable[str] = ()
    ) -> tuple[dict[str, Any] | None, int, str | None]:
        """Return the newest analysis matched by root session or evaluation run id."""
        identifiers = list(dict.fromkeys(
            value for value in [str(session_id), *(str(item) for item in correlation_ids)] if value
        ))
        query = {"sessionId": {"$in": identifiers}}
        total = self._rationality.count_documents(query)
        value = self._rationality.find_one(
            {**query, "status": {"$in": ["completed", "success", "ok"]}},
            sort=[("createTime", -1)],
        )
        if value is not None:
            value["_id"] = str(value.get("_id") or "")
            create_time = value.get("createTime")
            if isinstance(create_time, datetime):
                value["createTime"] = create_time.isoformat()
        matched_by = None
        if value is not None:
            matched_by = "root_session_id" if value.get("sessionId") == session_id else "evaluation_run_id"
        return value, total, matched_by

    def summary(self, *, start_time: datetime, end_time: datetime) -> dict[str, Any]:
        query = {"finished_at": {"$gte": start_time, "$lt": end_time}}
        pipeline = [
            {"$match": query},
            {"$group": {
                "_id": "$task_type",
                "sessions": {"$sum": 1},
                "average_tool_success_rate": {"$avg": "$metrics.tool_success_rate"},
                "average_skill_completeness": {"$avg": "$metrics.skill_completeness"},
                "error_count": {"$sum": "$metrics.error_count"},
                "retry_count": {"$sum": "$metrics.retry_attempt_count"},
                "suspected_fabrication_count": {"$sum": {"$ifNull": ["$metrics.suspected_fabrication_count", 0]}},
            }},
            {"$sort": {"sessions": -1}},
        ]
        groups = []
        for row in self._latest.aggregate(pipeline):
            row["task_type"] = row.pop("_id") or "other"
            groups.append(row)
        return {
            "total_sessions": sum(int(item["sessions"]) for item in groups),
            "groups": groups,
            "start_time": start_time,
            "end_time": end_time,
        }


def metrics_store_health() -> dict[str, Any]:
    try:
        MetricsStore()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "unavailable", "detail": str(exc)}
