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
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(os.environ.get("AGENT_EVAL_WORKERS", "2"))),
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
        job_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        job = {
            "job_id": job_id, "status": "queued", "phase": "queued", "progress": 0,
            "message": "Waiting for a worker", "created_at": now, "updated_at": now,
            "skill": skill_dir.name, "agent": request.get("agent"),
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
                run_id=job_id, progress_callback=on_progress, cancel_event=cancel,
            )
            self._update(job_id, status="completed", phase="completed", progress=100, result=result)
        except EvaluationCancelled as exc:
            self._update(job_id, status="cancelled", phase="cancelled", message=str(exc))
        except Exception as exc:
            self._update(job_id, status="failed", phase="failed", message="Evaluation failed", error=str(exc))

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted((dict(item) for item in self._jobs.values()), key=lambda x: x["created_at"], reverse=True)

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
