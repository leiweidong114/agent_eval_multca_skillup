from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from agent_eval.database import get_conversation
from app.config import BACKEND_ROOT
from app.metrics_store import MetricsStore
from app.schematic_rationality_judge import extract_rationality_metrics, judge_rationality_result
from app.session_metrics import calculate_rule_metrics
from app.session_metric_judge import judge_session_metrics
from app.session_task_classifier import classify_session_task, task_hierarchy


class MetricJobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="session-metrics")

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
            "session_ids": session_ids,
            "use_llm_judge": use_llm_judge,
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
            try:
                with self._lock:
                    job["current_session_id"] = session_id
                    job["current_session_index"] = session_index
                    job["phase"] = "loading_session"
                    job["progress"] = round((session_index - 1) / max(1, job["total"]) * 100)
                    self._append_event(job, "loading_session", "正在从 LiteLLM 数据库读取完整会话", session_id=session_id, session_index=session_index)
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
                    self._append_event(job, "session_loaded", "会话读取完成，开始执行确定性规则", session_id=session_id, interaction_count=conversation.get("interaction_count", len(conversation.get("timeline") or [])))
                    self._save(job, store)
                result = calculate_rule_metrics(conversation)
                rule_category, rule_subtype = task_hierarchy(str(result.get("task_type") or "other"))
                result["task_category"] = rule_category
                result["task_subtype"] = rule_subtype
                with self._lock:
                    self._append_event(job, "rule_metrics", "规则指标计算完成", session_id=session_id, task_type=result.get("task_type"))
                    self._save(job, store)
                if job["use_llm_judge"]:
                    with self._lock:
                        job["phase"] = "task_classification"
                        self._append_event(
                            job,
                            "task_classification_started",
                            "Judge LLM 正在根据第一条用户 Prompt 进行任务分类",
                            session_id=session_id,
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
                        )
                        self._save(job, store)
                    if classification_status == "completed":
                        result["task_type"] = classification["task_type"]
                        result["task_category"] = classification["task_category"]
                        result["task_subtype"] = classification.get("task_subtype")
                        result["task_type_source"] = "first_user_prompt_llm_judge"
                        result["task_type_confidence"] = classification.get("confidence")

                    def judge_progress(stage: str, chunk_index: int, chunk_total: int, message: str) -> None:
                        with self._lock:
                            job["phase"] = "llm_judge"
                            self._append_event(job, stage, message, session_id=session_id, chunk_index=chunk_index, chunk_total=chunk_total)
                            self._save(job, store)

                    result["judge"] = judge_session_metrics(
                        conversation,
                        employee_no=str(job.get("user_id") or "") or None,
                        progress_callback=judge_progress,
                    )
                    with self._lock:
                        judge_status = str(result["judge"].get("status") or "unknown")
                        self._append_event(
                            job,
                            "llm_judge_completed" if judge_status == "completed" else "llm_judge_unavailable",
                            "LLM Judge 分析完成" if judge_status == "completed" else "LLM Judge 不可用，保留规则指标",
                            session_id=session_id,
                            judge_status=judge_status,
                            detail=result["judge"].get("error"),
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
                    result["judge"] = {"status": "disabled"}
                    result["task_classification"] = {"status": "disabled"}
                    with self._lock:
                        self._append_event(job, "llm_judge_skipped", "本次任务未启用 LLM Judge", session_id=session_id)
                with self._lock:
                    job["phase"] = "schematic_rationality"
                    self._append_event(
                        job,
                        "schematic_rationality_loading",
                        "正在读取原理图合理性分析记录",
                        session_id=session_id,
                    )
                    self._save(job, store)
                rationality_record, rationality_record_count, rationality_matched_by = (
                    store.latest_rationality_analysis(
                        session_id,
                        correlation_ids=conversation.get("evaluation_run_ids") or [],
                    )
                )
                if rationality_record is None:
                    result["schematic_rationality"] = {
                        "status": "not_found",
                        "source_collection": "HDschematicRationalityCollection",
                        "record_count": rationality_record_count,
                    }
                    with self._lock:
                        self._append_event(
                            job,
                            "schematic_rationality_not_found",
                            "当前会话暂无统计原理图生成轨迹指标",
                            session_id=session_id,
                        )
                elif job["use_llm_judge"]:
                    try:
                        with self._lock:
                            self._append_event(
                                job,
                                "schematic_rationality_judge",
                                "Judge LLM 正在分析 resultText",
                                session_id=session_id,
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
                            "check_type": rationality_record.get("checkType"),
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
                            )
                else:
                    extracted = extract_rationality_metrics(rationality_record)
                    result["schematic_rationality"] = {
                        "status": "completed" if extracted.get("metrics") else "judge_disabled",
                        "judge_status": "disabled",
                        "source_collection": "HDschematicRationalityCollection",
                        "source_record_id": rationality_record.get("_id"),
                        "source_uuid": rationality_record.get("uuid"),
                        "source_create_time": rationality_record.get("createTime"),
                        "record_count": rationality_record_count,
                        "matched_by": rationality_matched_by,
                        "result_text": rationality_record.get("resultText"),
                        "quality_level": "unknown",
                        "summary": "已通过确定性脚本提取原理图轨迹指标；本次未启用 Judge LLM 中文解释。",
                        "issues": [],
                        **extracted,
                    }
                with self._lock:
                    job["phase"] = "saving_metrics"
                    self._append_event(job, "saving_metrics", "正在通过 Java 接口写入 MongoDB 会话指标", session_id=session_id)
                    self._save(job, store)
                store.upsert_metrics(result)
                with self._lock:
                    job["completed"] += 1
                    job["progress"] = round(session_index / max(1, job["total"]) * 100)
                    self._append_event(job, "session_completed", "会话指标计算完成", session_id=session_id, metric_status=result.get("status"))
            except Exception as exc:
                with self._lock:
                    job["failed"] += 1
                    job["errors"] = [*job["errors"], {"session_id": session_id, "detail": str(exc)}][-100:]
                    self._append_event(job, "session_failed", "会话指标计算失败", session_id=session_id, detail=str(exc))
            with self._lock:
                self._save(job, store)
        with self._lock:
            job["status"] = "completed" if not job["failed"] else "completed_with_errors"
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
        return {key: value for key, value in job.items() if key != "_id"}


metric_job_manager = MetricJobManager()
