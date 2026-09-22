"""Cross-session quality rollup from the latest saved metrics per Session ID."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from app.quality_summary import _display_rate, _rate


AGGREGATE_SESSION_ID = "__agent_eval_quality_aggregate__"
AGGREGATE_CHECK_TYPE = "agent_eval_quality_aggregate"
CATEGORIES = (
    ("hscope_diagram_lint", "总检查通过率", "框图规范检查总通过率", "overall_pass_rate"),
    ("hscope_block_corpus_check", "语料覆盖率", "语料覆盖率", "coverage_rate"),
    ("signal-interface-checker", "信号接口列表检查通过率", "信号接口列表检查通过率", "pass_rate"),
    ("tianshu-drc-review", "天枢DRC审查通过率", "天枢DRC审查通过率", "drc_pass_rate"),
)


def aggregate_quality_metrics(metrics: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Weight count-backed results; average only rate-only DRC values."""
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "total": 0})
    rate_only: dict[str, list[float]] = defaultdict(list)
    source_counts: dict[str, int] = defaultdict(int)
    quality_sessions: set[str] = set()
    for metric in metrics:
        summary = metric.get("quality_summary") or {}
        groups = summary.get("by_check_type") or {}
        flat = summary.get("rates") or {}
        compact = metric.get("agentEvalMetrics") or {}
        for check_type, source_label, result_label, count_key in CATEGORIES:
            group = groups.get(check_type) or {}
            source_count = (group.get("counts") or {}).get(count_key) or {}
            passed, total = source_count.get("passed"), source_count.get("total")
            if isinstance(passed, int) and isinstance(total, int) and total > 0 and 0 <= passed <= total:
                totals[result_label]["passed"] += passed
                totals[result_label]["total"] += total
                source_counts[result_label] += 1
                quality_sessions.add(str(metric.get("session_id") or ""))
                continue
            value = _rate(flat.get(source_label))
            if value is None:
                source_group = {
                    "hscope_diagram_lint": "框图规范检查",
                    "hscope_block_corpus_check": "语料库覆盖",
                    "signal-interface-checker": "信号接口列表检查",
                    "tianshu-drc-review": "天枢DRC审查",
                }[check_type]
                value = _rate((compact.get(source_group) or {}).get(source_label))
            if value is not None:
                rate_only[result_label].append(value)
                source_counts[result_label] += 1
                quality_sessions.add(str(metric.get("session_id") or ""))
    rates: dict[str, str | None] = {}
    methods: dict[str, str] = {}
    for _, _, label, _ in CATEGORIES:
        counts = totals[label]
        if counts["total"]:
            rates[label] = _display_rate(round(counts["passed"] / counts["total"] * 100, 2))
            methods[label] = "weighted_by_passed_total"
        elif rate_only[label]:
            rates[label] = _display_rate(round(sum(rate_only[label]) / len(rate_only[label]), 2))
            methods[label] = "mean_of_sessions_without_denominators"
        else:
            rates[label] = None
            methods[label] = "no_data"
    return {
        "rates": rates,
        "source_session_count": len(metrics),
        "quality_session_count": len(quality_sessions),
        "metric_session_counts": dict(source_counts),
        "counts": dict(totals),
        "rate_only_session_counts": {key: len(value) for key, value in rate_only.items()},
        "methods": methods,
    }
