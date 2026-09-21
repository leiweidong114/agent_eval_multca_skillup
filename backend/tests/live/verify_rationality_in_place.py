"""Recalculate two real quality reports and assert no MongoDB documents are added.

Run from backend with its runtime Python. This writes agentEvalMetrics and
agentEvalProcess into existing source records via the configured Java service.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.metric_job_manager import MetricJobManager
from app.metrics_store import MetricsStore


DEFAULT_SESSIONS = [
    "b8ad8d49-d362-4462-8318-174fb712e2e6",
    "da69826b-1a3a-4e56-91aa-97a9d88615be",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_ids", nargs="*", default=DEFAULT_SESSIONS)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args()
    store = MetricsStore()
    before = {sid: len(store._records_for([sid])) for sid in args.session_ids}
    manager = MetricJobManager()
    jobs = [manager.submit(session_ids=[sid], user_id="agent-eval-verification",
                           start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                           end_time=datetime(2027, 1, 1, tzinfo=timezone.utc),
                           use_llm_judge=not args.no_judge) for sid in args.session_ids]
    deadline = time.monotonic() + args.timeout
    previous = {}
    while time.monotonic() < deadline:
        current = {job["job_id"]: manager.get(job["job_id"]) for job in jobs}
        for job_id, value in current.items():
            state = (value["status"], value.get("phase"), value.get("current_session_id"))
            if previous.get(job_id) != state:
                print(json.dumps({"job_id": job_id, "state": state}, ensure_ascii=False), flush=True)
                previous[job_id] = state
        if all(value["status"] in {"completed", "completed_with_errors", "failed"}
               for value in current.values()):
            break
        time.sleep(5)
    else:
        print("Timed out waiting for Judge; jobs may still be running", flush=True)
        return 2
    okay = True
    for sid, job in zip(args.session_ids, jobs):
        value = current[job["job_id"]]
        records = store._records_for([sid])
        source = next((row for row in records if str(row.get("checkType") or "").strip()
                       in {"hscope_diagram_lint", "hscope_block_corpus_check"}), None)
        metric = (source or {}).get("agentEvalMetrics") or {}
        analysis = metric.get("schematic_rationality") or {}
        summary = {"session_id": sid, "job_status": value["status"], "errors": value.get("errors"),
                   "documents_before": before[sid], "documents_after": len(records),
                   "source_updated": bool(metric), "process_updated": bool((source or {}).get("agentEvalProcess")),
                   "analysis_type": analysis.get("analysis_type"), "analysis_status": analysis.get("status"),
                   "judge_status": analysis.get("judge_status") or ("completed" if analysis.get("model") else None),
                   "metrics": analysis.get("metrics")}
        print(json.dumps(summary, ensure_ascii=False, default=str), flush=True)
        okay &= len(records) == before[sid] and bool(metric) and bool((source or {}).get("agentEvalProcess"))
    return 0 if okay else 1


if __name__ == "__main__":
    raise SystemExit(main())
