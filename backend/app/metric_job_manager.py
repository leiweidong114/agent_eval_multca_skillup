from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from agent_eval.database import get_conversation
from agent_eval.model_config import resolve_config_secret
from app.config import BACKEND_ROOT
from app.metrics_store import MetricsStore, SESSION_METRICS_CHECK_TYPE, SESSION_PROCESS_CHECK_TYPE
from app.quality_summary import QUALITY_TYPES, judge_quality_summary, summarize_quality_records
from app.schematic_rationality_judge import extract_rationality_metrics, judge_rationality_result
from app.session_metrics import calculate_rule_metrics
from app.session_metric_judge import judge_session_metrics
from app.session_task_classifier import classify_session_task, first_user_prompt, task_hierarchy


def _judge_feature_enabled(name: str) -> bool:
    """Temporary per-stage switches; unset means disabled."""
    return resolve_config_secret(BACKEND_ROOT, name).lower() in {"1", "true", "yes", "on"}


def _rationality_record_summary(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    result_text = str(record.get("resultText") or "")
    return {
        "_id": str(record.get("_id") or "") or None,
        "uuid": record.get("uuid"),
        "status": record.get("status"),
        "createUser": record.get("createUser"),
        "createTime": record.get("createTime"),
        "checkType": record.get("checkType"),
        "checkMessage": record.get("checkMessage"),
        "userName": record.get("userName"),
        "hscopeProjectId": record.get("hscopeProjectId"),
        "boardNum": record.get("boardNum"),
        "sessionId": record.get("sessionId"),
        "resultTextPreview": result_text[:4000],
        "resultTextLength": len(result_text),
        "resultTextTruncated": len(result_text) > 4000,
    }


def _insert_response_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    record = value.get("record") if isinstance(value.get("record"), dict) else {}
    return {
        "response_fields": sorted(str(key) for key in value),
        "status": value.get("status") or value.get("message"),
        "record_id": record.get("_id") or value.get("_id"),
        "record_uuid": record.get("uuid"),
    }


def _rationality_analysis_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key != "result_text"
    }


def _metric_record_for_audit(record: dict[str, Any]) -> dict[str, Any]:
    """Return the MongoDB payload with resultText decoded for readable UI auditing."""
    value = dict(record)
    result_text = value.get("resultText")
    if isinstance(result_text, str):
        try:
            value["resultText"] = json.loads(result_text)
        except ValueError:
            pass
    return value


class MetricJobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="session-metrics")
        # One background session Judge per manager, with at most two concurrent
        # outbound Judge calls including classification/rationality.
        self._judge_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session-metric-judge")
        try:
            judge_parallelism = int(resolve_config_secret(BACKEND_ROOT, "SESSION_METRIC_JUDGE_PARALLELISM") or "2")
        except ValueError:
            judge_parallelism = 2
        self._judge_slots = threading.BoundedSemaphore(max(1, min(2, judge_parallelism)))

    def submit(
        self,
        *,
        session_ids: list[str],
        user_id: str,
        start_time: datetime,
        end_time: datetime,
        use_llm_judge: bool,
    ) -> dict[str, Any]:
        store = MetricsStore()
        now = datetime.now(timezone.utc)
        job = {
            "job_id": f"metrics-{uuid.uuid4().hex}",
            "status": "queued",
            "total": len(session_ids),
            "completed": 0,
            "failed": 0,
            "process_trace_failures": 0,
            "session_ids": session_ids,
            "use_llm_judge": use_llm_judge,
            "classification_judge_enabled": use_llm_judge and _judge_feature_enabled(
                "SESSION_METRICS_CLASSIFICATION_JUDGE_ENABLED"
            ),
            "conversation_judge_enabled": use_llm_judge and _judge_feature_enabled(
                "SESSION_METRICS_CONVERSATION_JUDGE_ENABLED"
            ),
            "user_id": user_id,
            "start_time": start_time,
            "end_time": end_time,
            "errors": [],
            "phase": "queued",
            "progress": 0,
            "current_session_id": None,
            "current_session_index": 0,
            "events": [],
            "event_seq": 0,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job["job_id"]] = job
        store.save_job(job)
        self._executor.submit(self._run, job["job_id"], store)
        return self._public(job)

    @staticmethod
    def _append_event(job: dict[str, Any], stage: str, message: str, **details: Any) -> None:
        job["event_seq"] = int(job.get("event_seq") or 0) + 1
        job["events"] = [
            *(job.get("events") or []),
            {
                "sequence": job["event_seq"],
                "timestamp": datetime.now(timezone.utc),
                "stage": stage,
                "message": message,
                **details,
            },
        ][-500:]

    def _save(self, job: dict[str, Any], store: MetricsStore) -> None:
        job["updated_at"] = datetime.now(timezone.utc)
        store.save_job(job)

    def _run(self, job_id: str, store: MetricsStore) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["phase"] = "loading_session"
            self._append_event(job, "job_started", "指标计算 Worker 已启动")
            self._save(job, store)
        for session_index, session_id in enumerate(list(job["session_ids"]), start=1):
            judge_future = None
            judge_active = threading.Event()
            judge_active.set()
            try:
                with self._lock:
                    job["current_session_id"] = session_id
                    job["current_session_index"] = session_index
                    job["phase"] = "loading_session"
                    job["progress"] = round((session_index - 1) / max(1, job["total"]) * 100)
                    self._append_event(
                        job,
                        "loading_session",
                        "正在从 LiteLLM PostgreSQL 读取完整会话",
                        session_id=session_id,
                        session_index=session_index,
                        outcome="running",
                        input={
                            "root_session_id": session_id,
                            "start_time": job["start_time"],
                            "end_time": job["end_time"],
                            "include_content": True,
                        },
                    )
                    self._save(job, store)
                conversation = get_conversation(
                    BACKEND_ROOT,
                    root_session_id=session_id,
                    user_id=None,
                    include_content=True,
                    start_time=job["start_time"],
                    end_time=job["end_time"],
                )
                if conversation is None:
                    raise ValueError("会话不存在于所选时间范围")
                with self._lock:
                    job["phase"] = "rule_metrics"
                    self._append_event(
                        job,
                        "session_loaded",
                        "LiteLLM 会话读取成功，开始执行确定性规则",
                        session_id=session_id,
                        interaction_count=conversation.get("interaction_count", len(conversation.get("timeline") or [])),
                        outcome="success",
                        output={
                            "root_session_id": conversation.get("root_session_id"),
                            "interaction_count": conversation.get("interaction_count", len(conversation.get("timeline") or [])),
                            "agent": conversation.get("agent"),
                            "models": conversation.get("models") or [],
                            "end_user": conversation.get("end_user"),
                            "started_at": conversation.get("started_at"),
                            "finished_at": conversation.get("finished_at"),
                            "total_tokens": conversation.get("total_tokens"),
                            "evaluation_run_ids": conversation.get("evaluation_run_ids") or [],
                        },
                    )
                    self._save(job, store)
                result = calculate_rule_metrics(conversation)
                rule_category, rule_subtype = task_hierarchy(str(result.get("task_type") or "other"))
                result["task_category"] = rule_category
                result["task_subtype"] = rule_subtype
                with self._lock:
                    self._append_event(
                        job,
                        "rule_metrics",
                        "确定性规则指标计算成功",
                        session_id=session_id,
                        task_type=result.get("task_type"),
                        outcome="success",
                        output={
                            "task_type": result.get("task_type"),
                            "task_type_source": result.get("task_type_source"),
                            "task_type_confidence": result.get("task_type_confidence"),
                            "metrics": result.get("metrics"),
                            "skill_steps": result.get("skill_steps"),
                            "evidence_coverage": result.get("evidence_coverage"),
                            "metric_definition_version": result.get("metric_definition_version"),
                        },
                    )
                    self._save(job, store)
                if job.get("conversation_judge_enabled", False):
                    def judge_progress(stage: str, chunk_index: int, chunk_total: int, message: str,
                                       *, active=judge_active, callback_session_id=session_id) -> None:
                        if not active.is_set():
                            return
                        with self._lock:
                            if not active.is_set():
                                return
                            self._append_event(
                                job, stage, message, session_id=callback_session_id,
                                chunk_index=chunk_index, chunk_total=chunk_total,
                                outcome="success" if stage.endswith("_completed") else "running",
                            )
                            self._save(job, store)

                    def run_session_judge(conversation_snapshot=conversation, callback=judge_progress,
                                          employee_no=str(job.get("user_id") or "") or None):
                        with self._judge_slots:
                            return judge_session_metrics(
                                conversation_snapshot, employee_no=employee_no,
                                progress_callback=callback,
                            )

                    judge_future = self._judge_executor.submit(run_session_judge)
                classification_status = "disabled"
                if job.get("classification_judge_enabled", False):
                    classification_prompt = first_user_prompt(conversation)
                    with self._judge_slots:
                        with self._lock:
                            job["phase"] = "task_classification"
                            self._append_event(
                                job,
                                "task_classification_started",
                                "Judge LLM 正在根据第一条用户 Prompt 进行任务分类",
                                session_id=session_id,
                                outcome="running",
                                input={
                                    "first_user_prompt": classification_prompt,
                                    "prompt_found": bool(classification_prompt),
                                    "root_session_id": session_id,
                                },
                            )
                            self._save(job, store)
                        result["task_classification"] = classify_session_task(
                            conversation,
                            employee_no=str(job.get("user_id") or "") or None,
                        )
                    classification = result["task_classification"]
                    with self._lock:
                        classification_status = str(classification.get("status") or "unknown")
                        self._append_event(
                            job,
                            "task_classification_completed" if classification_status == "completed" else "task_classification_unavailable",
                            "会话任务分类完成" if classification_status == "completed" else "会话任务分类不可用，保留规则分类",
                            session_id=session_id,
                            task_type=classification.get("task_type"),
                            detail=classification.get("error") or classification.get("reason"),
                            outcome="success" if classification_status == "completed" else "warning",
                            output=classification,
                        )
                        self._save(job, store)
                    if classification_status == "completed":
                        result["task_type"] = classification["task_type"]
                        result["task_category"] = classification["task_category"]
                        result["task_subtype"] = classification.get("task_subtype")
                        result["task_type_source"] = "first_user_prompt_llm_judge"
                        result["task_type_confidence"] = classification.get("confidence")

                else:
                    result["task_classification"] = {"status": "disabled", "reason": "任务分类 Judge 已暂时关闭"}
                    with self._lock:
                        self._append_event(job, "task_classification_skipped", "任务分类 Judge 已暂时关闭，保留规则分类",
                                           session_id=session_id, outcome="skipped")
                        self._save(job, store)
                with self._lock:
                    job["phase"] = "schematic_rationality"
                    query_ids = list(dict.fromkeys([
                        session_id,
                        *(str(value) for value in conversation.get("evaluation_run_ids") or []),
                    ]))
                    self._append_event(
                        job,
                        "schematic_rationality_loading",
                        "正在调用 Java 查询接口读取 MongoDB 原理图分析记录",
                        session_id=session_id,
                        input={
                            "collectionName": "HDschematicRationalityCollection",
                            "session_ids": query_ids,
                            "status_filter": "none",
                            "excluded_check_types": [SESSION_METRICS_CHECK_TYPE, SESSION_PROCESS_CHECK_TYPE],
                        },
                        interface={
                            "method": "GET",
                            "endpoint": store.query_endpoint(),
                            "collection": "HDschematicRationalityCollection",
                            "session_ids": query_ids,
                            "status": "calling",
                        },
                        outcome="running",
                    )
                    self._save(job, store)
                rationality_query_error: Exception | None = None
                try:
                    rationality_record, rationality_record_count, rationality_matched_by = store.latest_rationality_analysis(
                        session_id,
                        correlation_ids=conversation.get("evaluation_run_ids") or [],
                    )
                    rationality_query_diagnostic = store.latest_rationality_diagnostic()
                    with self._lock:
                        self._append_event(
                            job,
                            "schematic_data_query_succeeded",
                            "Java 查询接口调用成功",
                            session_id=session_id,
                            interface={
                                "method": "GET",
                                "endpoint": store.query_endpoint(),
                                "collection": "HDschematicRationalityCollection",
                                "session_ids": query_ids,
                                "status": "success",
                                "records_matched": rationality_record_count,
                                "matched_by": rationality_matched_by,
                                "calls": store.query_diagnostics(),
                                "selected_record": _rationality_record_summary(rationality_record),
                            },
                            outcome="success",
                            output={
                                "record_count": rationality_record_count,
                                "matched_by": rationality_matched_by,
                                "selected_record": _rationality_record_summary(rationality_record),
                                "selection_diagnostic": rationality_query_diagnostic,
                            },
                        )
                        self._save(job, store)
                except Exception as exc:
                    rationality_query_error = exc
                    rationality_record = None
                    rationality_record_count = 0
                    rationality_matched_by = None
                    rationality_query_diagnostic = {}
                    result["schematic_rationality"] = {
                        "status": "source_unavailable",
                        "source_collection": "HDschematicRationalityCollection",
                        "record_count": 0,
                        "error": str(exc),
                    }
                    with self._lock:
                        self._append_event(
                            job,
                            "schematic_data_query_failed",
                            "Java 查询接口调用失败",
                            session_id=session_id,
                            detail=str(exc),
                            interface={
                                "method": "GET",
                                "endpoint": store.query_endpoint(),
                                "collection": "HDschematicRationalityCollection",
                                "session_ids": query_ids,
                                "status": "failed",
                                "calls": store.query_diagnostics(),
                                "error": str(exc),
                            },
                            outcome="failed",
                            output=_rationality_analysis_summary(result["schematic_rationality"]),
                        )
                        self._save(job, store)
                if rationality_query_error is not None:
                    pass
                elif rationality_record is None:
                    result["schematic_rationality"] = {
                        "status": "not_found",
                        "source_collection": "HDschematicRationalityCollection",
                        "record_count": rationality_record_count,
                        "query_diagnostic": rationality_query_diagnostic,
                    }
                    with self._lock:
                        self._append_event(
                            job,
                            "schematic_rationality_not_found",
                            "当前会话暂无统计原理图生成轨迹指标",
                            session_id=session_id,
                            outcome="not_found",
                            input={
                                "collectionName": "HDschematicRationalityCollection",
                                "session_ids": query_ids,
                                "status_filter": "none",
                                "excluded_check_types": [SESSION_METRICS_CHECK_TYPE, SESSION_PROCESS_CHECK_TYPE],
                            },
                            output={
                                **_rationality_analysis_summary(result["schematic_rationality"]),
                                "query_succeeded": True,
                                "reason": rationality_query_diagnostic.get("reason")
                                or "接口调用成功，但没有找到 Session ID 匹配的原理图轨迹记录",
                                "selection_diagnostic": rationality_query_diagnostic,
                            },
                        )
                elif job["use_llm_judge"] and str(rationality_record.get("checkType") or "").strip() not in QUALITY_TYPES:
                    try:
                        with self._judge_slots:
                            with self._lock:
                                self._append_event(
                                    job,
                                    "schematic_rationality_judge",
                                    "Judge LLM 正在分析 resultText",
                                    session_id=session_id,
                                    outcome="running",
                                )
                                self._save(job, store)
                            analysis = judge_rationality_result(
                                rationality_record,
                                employee_no=str(job.get("user_id") or "") or None,
                            )
                        result["schematic_rationality"] = {
                            "source_collection": "HDschematicRationalityCollection",
                            "source_record_id": rationality_record.get("_id"),
                            "source_uuid": rationality_record.get("uuid"),
                            "source_create_time": rationality_record.get("createTime"),
                            "check_type": str(rationality_record.get("checkType") or "").strip(),
                            "check_message": rationality_record.get("checkMessage"),
                            "record_count": rationality_record_count,
                            "matched_by": rationality_matched_by,
                            "result_text": rationality_record.get("resultText"),
                            **analysis,
                        }
                        with self._lock:
                            self._append_event(
                                job,
                                "schematic_rationality_completed",
                                "原理图合理性指标分析完成",
                                session_id=session_id,
                                outcome="success",
                                output=_rationality_analysis_summary(result["schematic_rationality"]),
                            )
                    except Exception as exc:
                        extracted = extract_rationality_metrics(rationality_record)
                        result["schematic_rationality"] = {
                            "status": "completed" if extracted.get("metrics") else "judge_unavailable",
                            "judge_status": "unavailable",
                            "source_collection": "HDschematicRationalityCollection",
                            "source_record_id": rationality_record.get("_id"),
                            "source_uuid": rationality_record.get("uuid"),
                            "source_create_time": rationality_record.get("createTime"),
                            "check_type": str(rationality_record.get("checkType") or "").strip(),
                            "record_count": rationality_record_count,
                            "matched_by": rationality_matched_by,
                            "result_text": rationality_record.get("resultText"),
                            "quality_level": "unknown",
                            "summary": "已通过确定性脚本提取原理图轨迹指标；Judge LLM 中文解释暂不可用。",
                            "issues": [],
                            **extracted,
                            "error": str(exc),
                        }
                        with self._lock:
                            self._append_event(
                                job,
                                "schematic_rationality_judge_unavailable",
                                "原理图合理性 Judge 不可用，保留原始记录",
                                session_id=session_id,
                                detail=str(exc),
                                outcome="warning",
                                output=_rationality_analysis_summary(result["schematic_rationality"]),
                            )
                else:
                    extracted = extract_rationality_metrics(rationality_record)
                    result["schematic_rationality"] = {
                        "status": "completed" if extracted.get("metrics") else "judge_disabled",
                        "judge_status": "pending_quality_audit" if job["use_llm_judge"] else "disabled",
                        "source_collection": "HDschematicRationalityCollection",
                        "source_record_id": rationality_record.get("_id"),
                        "source_uuid": rationality_record.get("uuid"),
                        "source_create_time": rationality_record.get("createTime"),
                        "check_type": str(rationality_record.get("checkType") or "").strip(),
                        "record_count": rationality_record_count,
                        "matched_by": rationality_matched_by,
                        "result_text": rationality_record.get("resultText"),
                        "quality_level": "unknown",
                        "summary": "已通过确定性脚本提取原理图轨迹指标；四类质量检查将统一接受 Judge LLM 校验。" if job["use_llm_judge"] else "已通过确定性脚本提取原理图轨迹指标；本次未启用 Judge LLM 中文解释。",
                        "issues": [],
                        **extracted,
                    }
                    with self._lock:
                        self._append_event(
                            job,
                            "schematic_rationality_rules_completed",
                            "已通过确定性规则提取原理图合理性指标",
                            session_id=session_id,
                            outcome="success",
                            output=_rationality_analysis_summary(result["schematic_rationality"]),
                        )
                # The session quality summary reads every original checkType record,
                # including repeated reports of the same type. No source is changed.
                quality_records: list[dict[str, Any]] = []
                try:
                    quality_records = store.quality_records(session_id)
                    result["quality_summary"] = summarize_quality_records(session_id, quality_records)
                    if job["use_llm_judge"] and quality_records:
                        with self._lock:
                            self._append_event(job, "schematic_rationality_judge",
                                               "Judge LLM 正在校验四类质量指标",
                                               session_id=session_id, outcome="running",
                                               input={"record_count": len(quality_records),
                                                      "check_types": sorted({str(row.get("checkType") or "").strip() for row in quality_records})})
                            self._save(job, store)
                        try:
                            with self._judge_slots:
                                result["quality_summary"]["judge"] = judge_quality_summary(
                                    quality_records, result["quality_summary"],
                                    employee_no=str(job.get("user_id") or "") or None)
                            if result.get("schematic_rationality", {}).get("judge_status") == "pending_quality_audit":
                                result["schematic_rationality"]["judge_status"] = result["quality_summary"]["judge"]["status"]
                            with self._lock:
                                self._append_event(job, "schematic_rationality_completed",
                                                   "四类质量指标 Judge 校验完成",
                                                   session_id=session_id,
                                                   outcome=result["quality_summary"]["judge"]["status"],
                                                   output=result["quality_summary"]["judge"])
                                self._save(job, store)
                        except Exception as exc:
                            result["quality_summary"]["judge"] = {"status": "unavailable", "error": str(exc)}
                            if result.get("schematic_rationality", {}).get("judge_status") == "pending_quality_audit":
                                result["schematic_rationality"]["judge_status"] = "unavailable"
                            with self._lock:
                                self._append_event(job, "schematic_rationality_judge_unavailable",
                                                   "质量指标 Judge 不可用，保留规则提取值",
                                                   session_id=session_id, outcome="warning", detail=str(exc))
                                self._save(job, store)
                    with self._lock:
                        self._append_event(
                            job, "quality_summary_completed", "四类质量检查记录已逐条提取并汇总",
                            session_id=session_id, outcome="success",
                            output={"source_records": [
                                _rationality_record_summary(item) for item in quality_records
                            ], "quality_summary": result["quality_summary"]},
                        )
                        self._save(job, store)
                except Exception as exc:
                    result["quality_summary"] = {"session_id": session_id, "status": "source_unavailable",
                                                 "error": str(exc), "by_check_type": {}, "rates": {}}
                    with self._lock:
                        self._append_event(job, "quality_summary_failed", "质量指标汇总查询失败",
                                           session_id=session_id, outcome="warning", detail=str(exc))
                        self._save(job, store)
                result["board_num"] = next(
                    (
                        str(record.get("boardNum"))
                        for record in [*quality_records, rationality_record]
                        if isinstance(record, dict) and str(record.get("boardNum") or "").strip()
                    ),
                    session_id,
                )
                # The full-session Judge intentionally runs after the external
                # rationality lookup/analysis, making it the sixth visible step.
                # It still evaluates the LiteLLM conversation evidence only;
                # rationality analysis remains an independent fifth step.
                if job.get("conversation_judge_enabled", False):
                    with self._lock:
                        job["phase"] = "llm_judge"
                        self._save(job, store)
                    result["judge"] = judge_future.result()
                    with self._lock:
                        judge_status = str(result["judge"].get("status") or "unknown")
                        self._append_event(
                            job,
                            "llm_judge_completed" if judge_status == "completed" else "llm_judge_unavailable",
                            "LLM Judge 分析完成" if judge_status == "completed" else "LLM Judge 不可用，保留规则指标",
                            session_id=session_id,
                            judge_status=judge_status,
                            detail=result["judge"].get("error"),
                            outcome="success" if judge_status == "completed" else "warning",
                            output=result["judge"],
                        )
                        self._save(job, store)
                    if result["judge"].get("status") == "completed":
                        result["metrics"]["suspected_fabrication_count"] = len(
                            result["judge"].get("suspected_fabrications") or []
                        )
                        # Rule-derived task type stays authoritative when it has
                        # explicit Skill/script markers. Judge classification fills
                        # only the low-confidence fallback.
                        if classification_status != "completed" and result.get("task_type_source") == "rule_fallback":
                            result["task_type"] = result["judge"].get("task_type") or result["task_type"]
                            result["task_type_source"] = "llm_judge"
                            result["task_category"], result["task_subtype"] = task_hierarchy(result["task_type"])
                        result["status"] = "completed"
                    else:
                        result["status"] = "rules_completed_judge_unavailable"
                else:
                    result["judge"] = {"status": "disabled", "reason": "会话 Judge 已暂时关闭"}
                    with self._lock:
                        self._append_event(
                            job,
                            "llm_judge_skipped",
                            "本次任务未启用 LLM Judge",
                            session_id=session_id,
                            outcome="skipped",
                            output={"status": "disabled"},
                        )
                with self._lock:
                    trace_snapshot = {**job, "events": list(job.get("events") or [])}
                process_trace = store.build_process_trace(trace_snapshot, session_id)
                process_trace["result_summary"] = {
                    key: result.get(key)
                    for key in (
                        "session_id", "status", "started_at", "finished_at", "calculated_at",
                        "task_type", "task_category", "task_subtype", "agent", "model",
                        "end_user", "metric_definition_version", "metrics", "board_num",
                    )
                }
                metric_record = store.build_metrics_record(result, process_trace=process_trace)
                metric_record_audit = _metric_record_for_audit(metric_record)
                source = result.get("schematic_rationality") or {}
                persist_method = "POST"
                persist_endpoint = store.write_endpoint()
                with self._lock:
                    job["phase"] = "saving_metrics"
                    self._append_event(
                        job,
                        "saving_metrics",
                        "正在调用 Java 插入接口写入 MongoDB 会话指标汇总",
                        session_id=session_id,
                        input={
                            "collectionName": "HDschematicRationalityCollection",
                            "record": metric_record_audit,
                            "source_check_type": source.get("check_type"),
                            "target_field": "agentEvalMetrics",
                        },
                        interface={
                            "method": persist_method,
                            "endpoint": persist_endpoint,
                            "collection": "HDschematicRationalityCollection",
                            "session_id": session_id,
                            "check_type": SESSION_METRICS_CHECK_TYPE,
                            "target_field": "agentEvalMetrics",
                            "status": "calling",
                        },
                        outcome="running",
                    )
                    self._save(job, store)
                read_back_verification: dict[str, Any] | None = None
                try:
                    insert_response = store.upsert_metrics(result, record=metric_record)
                    read_back_verification = store.verify_metric_persisted(
                        session_id,
                        str(metric_record.get("uuid") or ""),
                    )
                    if not read_back_verification.get("verified"):
                        raise RuntimeError(
                            str(read_back_verification.get("reason") or "MongoDB 写后回读验证失败")
                        )
                except Exception as exc:
                    with self._lock:
                        self._append_event(
                            job,
                            "schematic_data_insert_failed",
                            "Java 写入或 MongoDB 写后回读验证失败，指标未确认保存",
                            session_id=session_id,
                            detail=str(exc),
                            interface={
                                "method": persist_method,
                                "endpoint": persist_endpoint,
                                "collection": "HDschematicRationalityCollection",
                                "session_id": session_id,
                                "check_type": SESSION_METRICS_CHECK_TYPE,
                                "target_field": "agentEvalMetrics",
                                "status": "failed",
                                "calls": store.write_diagnostics(),
                                "read_back_verification": read_back_verification,
                                "error": str(exc),
                            },
                            outcome="failed",
                        )
                        self._save(job, store)
                    raise
                with self._lock:
                    self._append_event(
                        job,
                        "schematic_data_insert_succeeded",
                        "Java 插入成功并通过 MongoDB 写后回读验证",
                        session_id=session_id,
                        interface={
                            "method": persist_method,
                            "endpoint": persist_endpoint,
                            "collection": "HDschematicRationalityCollection",
                            "session_id": session_id,
                            "check_type": SESSION_METRICS_CHECK_TYPE,
                            "target_field": "agentEvalMetrics",
                            "status": "success",
                            "calls": store.write_diagnostics(),
                            "response": _insert_response_summary(insert_response),
                            "read_back_verification": read_back_verification,
                        },
                        outcome="success",
                        output={
                            "session_id": session_id,
                            "saved_record": metric_record_audit,
                            "insert_response": _insert_response_summary(insert_response),
                            "read_back_verification": read_back_verification,
                            "metric_status": result.get("status"),
                            "task_type": result.get("task_type"),
                            "metrics": result.get("metrics"),
                            "schematic_rationality": _rationality_analysis_summary(result.get("schematic_rationality")),
                            "quality_summary": result.get("quality_summary"),
                        },
                    )
                    job["completed"] += 1
                    job["progress"] = round(session_index / max(1, job["total"]) * 100)
                    self._append_event(
                        job,
                        "session_completed",
                        "会话全部指标步骤完成",
                        session_id=session_id,
                        metric_status=result.get("status"),
                        outcome="success",
                        output={
                            "task_type": result.get("task_type"),
                            "metrics": result.get("metrics"),
                            "judge_status": (result.get("judge") or {}).get("status"),
                            "schematic_rationality_status": (result.get("schematic_rationality") or {}).get("status"),
                            "persisted_to": "HDschematicRationalityCollection",
                        },
                    )
                try:
                    aggregate = store.refresh_quality_aggregate()
                    with self._lock:
                        self._append_event(job, "quality_aggregate_updated",
                                           "全局累计质量指标已更新并从 MongoDB 回读",
                                           session_id=session_id, outcome="success",
                                           output={"rates": aggregate.get("rates"),
                                                   "source_session_count": aggregate.get("source_session_count"),
                                                   "updated_at": aggregate.get("updated_at"),
                                                   "mongo_record_id": aggregate.get("mongo_record_id"),
                                                   "aggregate_record_count": aggregate.get("aggregate_record_count"),
                                                   "java_interface_calls": aggregate.get("write_diagnostics")})
                        self._save(job, store)
                except Exception as exc:
                    with self._lock:
                        self._append_event(job, "quality_aggregate_update_failed",
                                           "单会话指标已保存，但全局累计指标刷新失败",
                                           session_id=session_id, outcome="warning", detail=str(exc))
                        self._save(job, store)
            except Exception as exc:
                judge_active.clear()
                if judge_future is not None:
                    judge_future.cancel()
                with self._lock:
                    job["failed"] += 1
                    job["errors"] = [*job["errors"], {"session_id": session_id, "detail": str(exc)}][-100:]
                    self._append_event(job, "session_failed", "会话指标计算失败", session_id=session_id, detail=str(exc), outcome="failed", output={"error": str(exc)})
            try:
                with self._lock:
                    trace_snapshot = {**job, "events": list(job.get("events") or [])}
                store.save_process_trace(trace_snapshot, session_id)
                with self._lock:
                    self._append_event(
                        job, "process_trace_saved", "计算过程已保存到 MongoDB",
                        session_id=session_id, outcome="success",
                    )
            except Exception as exc:
                with self._lock:
                    job["process_trace_failures"] += 1
                    job["errors"] = [
                        *job["errors"],
                        {"session_id": session_id, "detail": f"计算过程保存失败: {exc}"},
                    ][-100:]
                    self._append_event(
                        job, "process_trace_failed", "计算过程保存到 MongoDB 失败",
                        session_id=session_id, detail=str(exc), outcome="failed",
                    )
            with self._lock:
                self._save(job, store)
        with self._lock:
            job["status"] = (
                "completed" if not job["failed"] and not job["process_trace_failures"]
                else "completed_with_errors"
            )
            job["phase"] = "completed"
            job["progress"] = 100
            job["current_session_id"] = None
            job["finished_at"] = datetime.now(timezone.utc)
            self._append_event(job, "job_completed", "指标计算任务已结束", completed=job["completed"], failed=job["failed"])
            self._save(job, store)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return self._public(job)
        try:
            job = MetricsStore().get_job(job_id)
        except Exception:
            return None
        return self._public(job) if job else None

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {
            **{key: value for key, value in job.items() if key != "_id"},
            "server_time": datetime.now(timezone.utc),
        }


metric_job_manager = MetricJobManager()
