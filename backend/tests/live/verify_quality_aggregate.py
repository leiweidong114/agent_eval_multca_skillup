"""Insert one clearly labelled four-report fixture and verify the live rollup.

Run from the repository root with --apply. Re-running is idempotent for its
fixed test Session ID; source records are never deleted or overwritten.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.metrics_store import MetricsStore
from app.quality_aggregate import AGGREGATE_SESSION_ID
from app.quality_summary import compact_agent_eval_metrics, judge_quality_summary, summarize_quality_records
from app.schematic_data_client import RATIONALITY_COLLECTION


SESSION_ID = "agent-eval-quality-rollup-fixture-20260922"
LINT = """# 框图规范检查报告
## 图页：汇总回归测试 — 检查
检查1：block缺少器件标识（通过: 1/2 | 通过率: 50%）：
检查2：wire源末端缺失（通过: 1/2 | 通过率: 50%）：
检查3：port未属于block（通过: 1/2 | 通过率: 50%）：
检查4：port缺少name（通过: 1/2 | 通过率: 50%）：
检查5：同名block的partGroup不一致（通过: 1/2 | 通过率: 50%）：
附加：无连接的block（通过: 1/2 | 通过率: 50%）：
"""
FIXTURES = (
    ("hscope_diagram_lint", LINT),
    ("hscope_block_corpus_check", "block 条目总数: 2\n语料库有数据: 1\n语料库无数据: 1\n语料库覆盖率: 50%"),
    ("signal-interface-checker", "[INFO] BLOCK_INFO: 通过\n[INFO] 测试接口A: 通过\n[INFO] 测试接口B: 不通过\n统计: ERROR=1 WARN=0"),
    ("tianshu-drc-review", json.dumps({"status": "success", "result": {"drc_rate": "50%"}}, ensure_ascii=False)),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Explicitly authorize MongoDB fixture insertion")
    parser.add_argument("--judge", action="store_true", help="Also call the configured Judge LLM")
    args = parser.parse_args()
    store = MetricsStore()
    baseline = store.refresh_quality_aggregate() if args.apply else store.get_quality_aggregate()
    print(json.dumps({"phase": "before", "sessionId": AGGREGATE_SESSION_ID,
                      "aggregate": baseline and baseline.get("rates")}, ensure_ascii=False))
    existing = store.quality_records(SESSION_ID)
    prior_metric = store.get_metrics(SESSION_ID) if existing else None
    if args.apply and len(existing) == 4 and prior_metric is not None:
        if (prior_metric.get("agentEvalMetrics") or {}).get("信号接口列表检查", {}).get("信号接口列表检查通过率") != "50.00%":
            raise RuntimeError("已有同名测试会话，但指标内容不匹配；停止以免覆盖用户数据")
        print(json.dumps({"phase": "already_verified", "source_session_id": SESSION_ID,
                          "aggregate_session_id": AGGREGATE_SESSION_ID,
                          "aggregate_record_id": baseline.get("mongo_record_id"),
                          "rates": baseline.get("rates")}, ensure_ascii=False))
        return
    if not existing and args.apply:
        client = store._data_client()
        for check_type, result_text in FIXTURES:
            now = datetime.now(timezone.utc).isoformat()
            client.insert_record({
                "uuid": uuid.uuid4().hex, "status": "completed", "createUser": "agent-eval-fixture",
                "createTime": now, "checkType": check_type, "checkMessage": "Agent Eval 汇总回归测试记录",
                "userName": "Agent Eval", "hscopeProjectId": "quality-rollup-fixture",
                "boardNum": "fixture-20260922", "sessionId": SESSION_ID, "resultText": result_text,
            }, collection_name=RATIONALITY_COLLECTION)
        existing = store.quality_records(SESSION_ID)
    if len(existing) not in (0, 4):
        raise RuntimeError(f"测试会话原始记录不完整：{len(existing)}/4；停止，避免重复插入")
    print(json.dumps({"phase": "source", "sessionId": SESSION_ID, "record_count": len(existing),
                      "check_types": sorted(str(row.get("checkType") or "").strip() for row in existing)}, ensure_ascii=False))
    if not args.apply:
        return
    quality = summarize_quality_records(SESSION_ID, existing)
    expected = {"总检查通过率": "50.00%", "语料覆盖率": "50.00%",
                "信号接口列表检查通过率": "50.00%", "天枢DRC审查通过率": "50.00%"}
    for key, value in expected.items():
        if quality["rates"].get(key) != value:
            raise RuntimeError(f"测试数据解析错误：{key}={quality['rates'].get(key)}，期望 {value}")
    if args.judge:
        quality["judge"] = judge_quality_summary(existing, quality)
        if quality["judge"]["status"] != "verified":
            raise RuntimeError(f"Judge 与规则值不一致：{quality['judge']['disagreements']}")
    result = {"session_id": SESSION_ID, "status": "completed", "end_user": "agent-eval-fixture",
              "task_type": "aggregate_regression_fixture", "metric_definition_version": "fixture-20260922",
              "quality_summary": quality, "calculated_at": datetime.now(timezone.utc).isoformat()}
    record = store.build_metrics_record(result)
    store.upsert_metrics(result, record=record)
    verified = store.verify_metric_persisted(SESSION_ID, record["uuid"])
    saved = store.get_metrics(SESSION_ID) or {}
    if not verified["verified"] or saved.get("agentEvalMetrics") != compact_agent_eval_metrics(quality):
        raise RuntimeError(f"新会话指标写入或回读失败：{verified}")
    rollup = store.refresh_quality_aggregate()
    if (rollup.get("session_id") != AGGREGATE_SESSION_ID
            or rollup.get("mongo_record_id") != baseline.get("mongo_record_id")
            or rollup.get("aggregate_record_count") != 1):
        raise RuntimeError("新会话计算后，汇总结果未保持原记录唯一更新")
    for label, source in (("框图规范检查总通过率", "overall_pass_rate"),
                          ("语料覆盖率", "coverage_rate"),
                          ("信号接口列表检查通过率", "pass_rate")):
        previous = (baseline.get("counts") or {}).get(label) or {"passed": 0, "total": 0}
        fixture_count = next((group.get("counts", {}).get(source) for group in quality["by_check_type"].values()
                              if group.get("counts", {}).get(source)), None)
        if fixture_count:
            expected_count = {key: previous.get(key, 0) + fixture_count[key] for key in ("passed", "total")}
            if rollup["counts"].get(label) != expected_count:
                raise RuntimeError(f"累计分子分母错误：{label}: {rollup['counts'].get(label)} != {expected_count}")
    print(json.dumps({"phase": "verified", "source_session_id": SESSION_ID,
                      "metric_record_id": verified["record_id"], "aggregate_session_id": AGGREGATE_SESSION_ID,
                      "aggregate_record_id": rollup["mongo_record_id"], "quality_session_count": rollup["quality_session_count"],
                      "rates": rollup["rates"], "judge": quality.get("judge", {}).get("status")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
