"""Recalculate MongoDB quality summaries for exact Session IDs.

Dry-run by default. Pass --apply to insert a new agent_eval_session_metrics
record with the quality JSON in agentEvalMetrics, then read it back.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.metrics_store import MetricsStore
from app.quality_summary import compact_agent_eval_metrics, judge_quality_summary, summarize_quality_records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_ids", nargs="+")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--judge", action="store_true", help="Ask the configured Judge LLM to audit all four report types")
    args = parser.parse_args()
    store = MetricsStore()
    for session_id in args.session_ids:
        rows = store.quality_records(session_id)
        quality = summarize_quality_records(session_id, rows)
        if args.judge and rows:
            try:
                quality["judge"] = judge_quality_summary(rows, quality)
            except Exception as exc:
                quality["judge"] = {"status": "unavailable", "error": str(exc)}
        print(json.dumps({"session_id": session_id, "source_record_count": len(rows),
                          "rates": quality["rates"], "judge": quality.get("judge")}, ensure_ascii=False))
        if not args.apply:
            continue
        previous = store.get_metrics(session_id) or {}
        result = {key: previous[key] for key in (
            "status", "started_at", "finished_at", "calculated_at", "task_type",
            "task_category", "task_subtype", "agent", "model", "end_user",
            "metric_definition_version", "metrics", "judge", "task_classification",
            "source_fingerprint",
        ) if key in previous}
        result.update(session_id=session_id, status="completed", quality_summary=quality)
        record = store.build_metrics_record(result)
        store.upsert_metrics(result, record=record)
        verification = store.verify_metric_persisted(session_id, record["uuid"])
        saved = store.get_metrics(session_id) or {}
        compact = compact_agent_eval_metrics(quality)
        if (not verification["verified"] or saved.get("quality_summary", {}).get("rates") != quality["rates"]
                or saved.get("agentEvalMetrics") != compact):
            raise RuntimeError(f"MongoDB summary read-back failed: {session_id}: {verification}")
        print(json.dumps({"session_id": session_id, "status": "verified",
                          "record_id": verification["record_id"], "agentEvalMetrics": saved["agentEvalMetrics"]},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
