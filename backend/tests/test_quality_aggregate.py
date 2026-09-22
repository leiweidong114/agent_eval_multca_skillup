from app.quality_aggregate import aggregate_quality_metrics
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
    result = aggregate_quality_metrics([_metric("a", 1, 2, 50), _metric("b", 9, 10, 100)])
    assert result["rates"]["框图规范检查总通过率"] == "83.33%"
    assert result["rates"]["天枢DRC审查通过率"] == "75.00%"
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
    assert first["rates"]["框图规范检查总通过率"] == "83.33%"
    assert first["rates"]["天枢DRC审查通过率"] == "75.00%"
    assert store.get_quality_aggregate()["rates"] == first["rates"]
    record_count = len(client.records)
    assert store.refresh_quality_aggregate()["mongo_record_id"] == first["mongo_record_id"]
    assert len(client.records) == record_count
    # Recalculation inserts a newer metric for the same Session ID; old value
    # must not be counted twice.
    store.upsert_metrics(_metric("s1", 2, 2, 100))
    second = store.refresh_quality_aggregate()
    assert second["rates"]["框图规范检查总通过率"] == "91.67%"
    assert second["source_session_count"] == 2
