from app.quality_aggregate import AGGREGATE_SESSION_ID, aggregate_quality_metrics
from app.metrics_store import MetricsStore


def _metric(session, lint_passed, lint_total, drc):
    return {
        "session_id": session,
        "quality_summary": {
            "rates": {"总检查通过率": f"{lint_passed / lint_total * 100:.2f}%",
                      "天枢DRC审查通过率": f"{drc:.2f}%"},
            "by_check_type": {
                "hscope_diagram_lint": {"counts": {"overall_pass_rate": {"passed": lint_passed, "total": lint_total}}},
            },
        },
    }


def test_cumulative_rates_weight_denominators_and_average_rate_only_drc():
    assert AGGREGATE_SESSION_ID == "汇总结果"
    result = aggregate_quality_metrics([_metric("a", 1, 2, 50), _metric("b", 9, 10, 100)])
    assert result["rates"]["框图规范检查总通过率"] == "83.33%"
    assert result["rates"]["天枢 DRC 审查通过率"] == "75.00%"
    assert result["rates"]["语料覆盖率"] is None
    assert result["source_session_count"] == 2


def test_rollup_persists_and_reads_back_latest_session_metrics_only():
    from test_metrics_store_remote import FakeClient

    client = FakeClient()
    store = MetricsStore.__new__(MetricsStore)
    store._client = client
    for session, passed, total, drc in (("s1", 1, 2, 50), ("s2", 9, 10, 100)):
        metric = _metric(session, passed, total, drc)
        store.upsert_metrics(metric)
    first = store.refresh_quality_aggregate()
    assert client.records[-1]["sessionId"] == "汇总结果"
    assert first["rates"]["框图规范检查总通过率"] == "83.33%"
    assert first["rates"]["天枢 DRC 审查通过率"] == "75.00%"
    assert set(client.records[-1]["agentEvalMetrics"]) == {
        "框图规范检查总通过率", "语料覆盖率", "信号接口列表检查通过率", "天枢 DRC 审查通过率",
    }
    assert first["aggregate_record_count"] == 1
    assert [item["method"] for item in first["write_diagnostics"]] == ["DELETE", "POST"]
    assert store.get_quality_aggregate()["rates"] == first["rates"]
    record_count = len(client.records)
    refreshed = store.refresh_quality_aggregate()
    assert refreshed["mongo_uuid"] != first["mongo_uuid"]
    assert len(client.records) == record_count
    # Recalculation inserts a newer metric for the same Session ID; old value
    # must not be counted twice.
    store.upsert_metrics(_metric("s1", 2, 2, 100))
    before_refresh_count = len(client.records)
    second = store.refresh_quality_aggregate()
    assert second["rates"]["框图规范检查总通过率"] == "91.67%"
    assert second["source_session_count"] == 2
    assert second["mongo_uuid"] != refreshed["mongo_uuid"]
    assert len(client.records) == before_refresh_count
