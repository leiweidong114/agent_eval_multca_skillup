"""Replace unverified Judge numerics in existing source-document summaries.

Dry-run by default. --apply updates only agentEvalMetrics on the exact source
record identified by sessionId and UUID; it never inserts a document.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.metrics_store import MetricsStore
from app.schematic_rationality_judge import (
    _verified_issues, _verified_summary, extract_rationality_metrics,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_ids", nargs="+", help="Exact LiteLLM root Session IDs")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    store = MetricsStore()
    for session_id in args.session_ids:
        rows = store._records_for([session_id])
        source = next((row for row in rows if str(row.get("checkType") or "").strip()
                       in {"hscope_diagram_lint", "hscope_block_corpus_check"}
                       and isinstance(row.get("agentEvalMetrics"), dict)), None)
        if not source:
            raise RuntimeError(f"No existing source metrics document: {session_id}")
        document = dict(source["agentEvalMetrics"])
        analysis = dict(document["schematic_rationality"])
        extracted = extract_rationality_metrics(source)
        analysis.setdefault("judge_unverified_summary", analysis.get("summary"))
        analysis.setdefault("judge_unverified_issues", analysis.get("issues"))
        analysis["metrics"] = extracted["metrics"]
        analysis["summary"] = _verified_summary(extracted["analysis_type"], extracted["metrics"])
        analysis["issues"] = _verified_issues(extracted["analysis_type"], extracted["metrics"])
        analysis["check_type"] = extracted["analysis_type"]
        document["schematic_rationality"] = analysis
        before = len(rows)
        if args.apply:
            store._data_client().update_record(session_id=session_id,
                                               check_type=extracted["analysis_type"],
                                               field="agentEvalMetrics", value=document)
            check = store._records_for([session_id])
            if len(check) != before or not any(str(row.get("checkType") or "").strip() == extracted["analysis_type"]
                and (row.get("agentEvalMetrics") or {}).get("schematic_rationality", {}).get("summary")
                == analysis["summary"] for row in check):
                raise RuntimeError(f"Write-back verification failed: {session_id}")
        print(json.dumps({"session_id": session_id, "applied": args.apply,
                          "documents_before": before, "documents_after": len(store._records_for([session_id])),
                          "summary": analysis["summary"], "model": analysis.get("model")},
                         ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
