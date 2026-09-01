from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .adapters import get_adapter
from .config import Experiment
from .models import Candidate, RunRecord, Task
from .reporting import write_reports
from .scoring import score_result


def run_experiment(experiment: Experiment) -> tuple[Path, list[RunRecord]]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = experiment.output_dir / f"{experiment.id}-{stamp}"
    workspaces = output_dir / "workspaces"
    workspaces.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[Candidate, Task, int]] = []
    for candidate in experiment.candidates:
        for task in experiment.tasks:
            if _compatible(candidate, task):
                for repeat in range(1, experiment.repeats + 1):
                    jobs.append((candidate, task, repeat))

    records: list[RunRecord] = []
    with ThreadPoolExecutor(max_workers=experiment.concurrency) as pool:
        futures = {
            pool.submit(
                _run_one,
                experiment,
                candidate,
                task,
                repeat,
                workspaces,
            ): (candidate.id, task.id, repeat)
            for candidate, task, repeat in jobs
        }
        total = len(futures)
        for index, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            print(
                f"[{index}/{total}] {record.candidate_id} / {record.task_id} "
                f"/ r{record.repeat}: "
                f"{'PASS' if record.passed else 'FAIL'} "
                f"({record.wall_duration_ms / 1000:.2f}s)",
                flush=True,
            )
    records.sort(key=lambda item: (item.task_id, item.candidate_id, item.repeat))
    write_reports(records, output_dir)
    return output_dir, records


def _compatible(candidate: Candidate, task: Task) -> bool:
    if candidate.adapter in {"direct_http", "claude_cli_direct"}:
        return task.kind == "direct"
    return task.kind == "repo"


def _run_one(
    experiment: Experiment,
    candidate: Candidate,
    task: Task,
    repeat: int,
    workspaces: Path,
) -> RunRecord:
    run_id = str(uuid.uuid4())
    workdir: Path | None = None
    before: dict[str, str] = {}
    if task.kind == "repo" and task.fixture:
        safe_name = f"{task.id}__{candidate.id}__r{repeat}__{run_id[:8]}"
        workdir = workspaces / safe_name
        shutil.copytree(
            task.fixture,
            workdir,
            ignore=shutil.ignore_patterns(".maeval", "__pycache__", "*.pyc"),
        )
        before = _snapshot(workdir)

    started_at = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()
    adapter = get_adapter(candidate.adapter)
    adapter_result = adapter.run(candidate, task, workdir)
    score = score_result(task, adapter_result, workdir)
    wall_ms = int((time.perf_counter() - start) * 1000)
    after = _snapshot(workdir) if workdir else {}
    changed_files = sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )
    passed = adapter_result.ok and score.passed
    return RunRecord(
        run_id=run_id,
        experiment_id=experiment.id,
        candidate_id=candidate.id,
        adapter=candidate.adapter,
        requested_model=candidate.model,
        actual_model=adapter_result.actual_model,
        task_id=task.id,
        task_kind=task.kind,
        tags=list(task.tags),
        repeat=repeat,
        started_at=started_at,
        wall_duration_ms=wall_ms,
        adapter_ok=adapter_result.ok,
        passed=passed,
        score=score.score if adapter_result.ok else 0.0,
        score_detail=score.detail,
        response_text=adapter_result.text,
        error=adapter_result.error,
        duration_api_ms=adapter_result.duration_api_ms,
        test_duration_ms=score.test_duration_ms,
        input_tokens=adapter_result.input_tokens,
        output_tokens=adapter_result.output_tokens,
        cache_read_tokens=adapter_result.cache_read_tokens,
        cost_usd=adapter_result.cost_usd,
        changed_files=changed_files,
        workdir=str(workdir) if workdir else None,
    )


def _snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        result[str(relative).replace("\\", "/")] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return result
