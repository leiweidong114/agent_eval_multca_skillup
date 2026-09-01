from __future__ import annotations

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_eval.runner import EvaluationCancelled, run_evaluation
from app.config import BACKEND_ROOT, RUNS_ROOT


class EvaluationJobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._max_workers = max(1, int(os.environ.get("AGENT_EVAL_WORKERS", "2")))
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="agent-eval",
        )
        self._state_dir = RUNS_ROOT / "_jobs"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    def _load_existing(self) -> None:
        for path in self._state_dir.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job.get("status") in {"queued", "running", "cancelling"}:
                    job.update(status="interrupted", message="Service restarted during evaluation")
                self._jobs[job["job_id"]] = job
            except (OSError, ValueError, KeyError):
                continue

    def _save(self, job: dict[str, Any]) -> None:
        (self._state_dir / f"{job['job_id']}.json").write_text(
            json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def submit(self, request: dict[str, Any], skill_dir: Path) -> dict[str, Any]:
        task_id = uuid.uuid4().hex
        job_id = task_id  # Backward-compatible alias for existing clients.
        now = datetime.now().isoformat()
        job = {
            "job_id": job_id, "task_id": task_id,
            "client_task_id": request.get("client_task_id"),
            "status": "queued", "phase": "queued", "progress": 0,
            "message": "Waiting for a worker", "created_at": now, "updated_at": now,
            "skill": skill_dir.name, "skills": request.get("skills") or [skill_dir.name],
            "evaluation_type": request.get("evaluation_type", "skill"), "agent": request.get("agent"),
            "user_id": request.get("user_id", "local"),
            "task_name": request.get("task_name") or skill_dir.name,
            "model": request.get("model"), "profile": request.get("profile"),
            "result": None, "error": None,
        }
        cancel = threading.Event()
        with self._lock:
            self._jobs[job_id] = job
            self._cancel[job_id] = cancel
            self._save(job)
        self._executor.submit(self._run, job_id, request, skill_dir, cancel)
        return dict(job)

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(values, updated_at=datetime.now().isoformat())
            self._save(job)

    def _run(self, job_id: str, request: dict[str, Any], skill_dir: Path, cancel: threading.Event) -> None:
        self._update(job_id, status="running", phase="preparing", progress=1)

        def on_progress(phase: str, percent: int, message: str) -> None:
            self._update(job_id, phase=phase, progress=percent, message=message)

        try:
            result = run_evaluation(
                project_root=BACKEND_ROOT,
                skill_dir=str(skill_dir),
                agent=request["agent"], model=request.get("model"), profile=request.get("profile"),
                case_files=request.get("case"), prompt=request.get("prompt"),
                executable=request.get("agent_executable"),
                must_contain=request.get("must_contain"), must_not_contain=request.get("must_not_contain"),
                parallelism=request.get("parallelism", 1), iterations=request.get("iterations", 1),
                timeout_seconds=request.get("timeout_seconds", 1800), max_turns=request.get("max_turns", 12),
                benchmark=request.get("benchmark", True), output_dir=str(RUNS_ROOT),
                extra_args=request.get("extra_args"), validate_only=False,
                collect_database_trace=request.get("collect_database_trace", True),
                require_model_verification=request.get("require_model_verification", True),
                run_id=job_id, task_id=job_id,
                progress_callback=on_progress, cancel_event=cancel,
                user_id=request.get("user_id", "local"),
                task_name=request.get("task_name") or skill_dir.name,
                client_task_id=request.get("client_task_id"),
                run_llm_judge_enabled=request.get("llm_judge", True),
                evaluation_type=request.get("evaluation_type", "skill"),
                selected_skills=request.get("skills") or [skill_dir.name],
            )
            status = "completed" if result.get("status", "completed") == "completed" else "failed"
            self._update(
                job_id, status=status, phase=status, progress=100, result=result,
                message="Evaluation completed" if status == "completed" else "Evaluation failed",
            )
        except EvaluationCancelled as exc:
            self._update(job_id, status="cancelled", phase="cancelled", message=str(exc))
        except Exception as exc:
            self._update(job_id, status="failed", phase="failed", message="Evaluation failed", error=str(exc))

    def list(self, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = (dict(item) for item in self._jobs.values())
            if user_id is not None:
                items = (item for item in items if item.get("user_id") == user_id)
            return sorted(items, key=lambda x: x["created_at"], reverse=True)

    def capacity(self) -> dict[str, Any]:
        with self._lock:
            running = sum(item.get("status") == "running" for item in self._jobs.values())
            queued = sum(item.get("status") == "queued" for item in self._jobs.values())
        return {
            "top_level_workers": self._max_workers,
            "running_jobs": running,
            "queued_jobs": queued,
            "per_job_case_parallelism_max": 16,
            "scheduler": "local_thread_pool_plus_skill_up",
            "uses_multica_server_scheduler": False,
        }

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job["status"] not in {"queued", "running"}:
                return dict(job)
            self._cancel[job_id].set()
            job.update(status="cancelling", phase="cancelling", message="Cancellation requested")
            self._save(job)
            return dict(job)


job_manager = EvaluationJobManager()
