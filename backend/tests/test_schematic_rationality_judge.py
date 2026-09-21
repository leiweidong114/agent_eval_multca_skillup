import json

from app import schematic_rationality_judge as subject
from app.metrics_store import MetricsStore


def test_source_numeric_metrics_override_judge_and_are_clamped(monkeypatch):
    monkeypatch.setattr(
        subject,
        "run_json_judge",
        lambda **kwargs: {
            "model": "judge-model",
            "judge_interaction_id": "judge-1",
            "usage": {"total_tokens": 42},
            "result": {
                "quality_level": "good",
                "metrics": {"overall_score": 12, "signal_integrity": 88},
                "summary": "质量总体良好。",
                "issues": [{"severity": "low", "category": "ERC", "message": "存在告警"}],
            },
        },
    )
    record = {
        "sessionId": "run-1",
        "resultText": json.dumps({
            "overall_score": 120,
            "erc_error_count": -3,
        }),
    }

    result = subject.judge_rationality_result(record, employee_no="001")

    assert result["status"] == "completed"
    assert result["metrics"]["overall_score"] == 100
    assert result["metrics"]["erc_error_count"] == 0
    assert result["metrics"]["signal_integrity"] == 88
    assert result["summary"] == "质量总体良好。"


def test_text_source_uses_only_judge_extracted_metrics(monkeypatch):
    monkeypatch.setattr(
        subject,
        "run_json_judge",
        lambda **kwargs: {
            "result": {
                "quality_level": "fair",
                "metrics": {"warning_count": 2, "unknown_metric": 99},
                "summary": "有两项告警。",
                "issues": [],
            }
        },
    )

    result = subject.judge_rationality_result({"sessionId": "s", "resultText": "检测到两项告警"})

    assert result["source_format"] == "text"
    assert result["metrics"] == {"warning_count": 2}


def test_hscope_diagram_lint_rates_are_extracted_by_code(monkeypatch):
    observed = {}

    def fake_judge(**kwargs):
        observed.update(kwargs)
        return {
            "model": "judge-model",
            "result": {
                "quality_level": "good",
                "metrics": {"overall_success_rate": 1},
                "summary": "六项检查总体稳定。",
                "issues": [],
            },
        }

    monkeypatch.setattr(subject, "run_json_judge", fake_judge)
    record = {
        "sessionId": "session-lint",
        "checkType": "hscope_diagram_lint",
        "resultText": json.dumps({
            "totalSuccessRate": 92.5,
            "checks": [
                {"name": "器件检查", "successRate": 95},
                {"name": "网络检查", "successRate": 0.9},
                {"name": "电源检查", "successRate": 88},
                {"name": "接口检查", "successRate": 91},
                {"name": "标注检查", "successRate": 93},
                {"name": "布局检查", "successRate": 87},
            ],
        }, ensure_ascii=False),
    }

    result = subject.judge_rationality_result(record, employee_no="001")

    assert result["analysis_type"] == "hscope_diagram_lint"
    assert result["metrics"]["overall_success_rate"] == 92.5
    assert result["metrics"]["extraction_complete"] is True
    assert [item["success_rate"] for item in result["metrics"]["dimension_success_rates"]] == [95, 90, 88, 91, 93, 87]
    assert "checkType=hscope_diagram_lint" in observed["user_prompt"]
    assert "脚本已提取的权威数值" in observed["user_prompt"]


class FakeDataClient:
    def find_rationality_records(self, identifiers):
        assert identifiers == ["root-session", "run-42"]
        return [{
            "_id": "mongo-1",
            "sessionId": "run-42",
            "status": "completed",
            "resultText": "{}",
        }]


def test_store_can_join_rationality_record_by_evaluation_run_id(monkeypatch):
    monkeypatch.setattr("app.metrics_store.SchematicDataClient", FakeDataClient)
    store = MetricsStore.__new__(MetricsStore)

    record, count, matched_by = store.latest_rationality_analysis(
        "root-session", correlation_ids=["run-42"]
    )

    assert count == 1
    assert record["_id"] == "mongo-1"
    assert matched_by == "evaluation_run_id"
    assert store.latest_rationality_diagnostic()["eligible_record_count"] == 1


class IneligibleDataClient:
    def find_rationality_records(self, identifiers):
        return [{
            "_id": "mongo-2",
            "sessionId": identifiers[0],
            "status": "pending",
            "checkType": "hscope_diagram_lint",
            "resultText": "{}",
        }]


def test_store_accepts_matching_session_regardless_of_status(monkeypatch):
    monkeypatch.setattr("app.metrics_store.SchematicDataClient", IneligibleDataClient)
    store = MetricsStore.__new__(MetricsStore)

    record, count, matched_by = store.latest_rationality_analysis("root-session")
    diagnostic = store.latest_rationality_diagnostic()

    assert record["_id"] == "mongo-2"
    assert count == 1
    assert matched_by == "root_session_id"
    assert diagnostic["exact_match_record_count"] == 1
    assert diagnostic["status_counts"] == {"pending": 1}
    assert diagnostic["status_filter"] == "none"
    assert diagnostic["eligible_record_count"] == 1
    assert diagnostic["reason"] is None


def test_block_corpus_report_with_padded_check_type_extracts_counts():
    record = {"checkType": "hscope_block_corpus_check  ", "resultText": """
【hscope block 语料库覆盖检查报告】 project_id=201
  图页总数: 12
  block 条目总数: 12
  唯一编码数: 12
  语料库有数据: 9 (9/12)
  语料库无数据: 3 (3/12)
  不在语料库比例: 25.0%
  语料库覆盖率: 75.0%
不在语料库中的编码列表（共 3 个）:
----------------------------------------
  13040588
    所在图页: TXFB1
  0302104397
  0302094006
在语料库中的编码样例（前 20 个）:
"""}
    result = subject.extract_rationality_metrics(record)
    assert result["analysis_type"] == "hscope_block_corpus_check"
    assert result["metrics"]["coverage_rate"] == 75.0
    assert result["metrics"]["missing_codes"] == ["13040588", "0302104397", "0302094006"]
    assert result["metrics"]["counts_consistent"] is True


def test_diagram_lint_markdown_report_aggregates_observed_checks():
    record = {"checkType": "hscope_diagram_lint", "resultText": """# 框图规范检查报告
## 图页：甲 — ❌ 发现问题
检查1：block缺少器件标识（通过: 1/2 | 通过率: 50.0%）：
附加：无连接的block（通过: 2/2 | 通过率: 100.0%）：
## 图页：乙 — ❌ 发现问题
检查1：block缺少器件标识（通过: 2/4 | 通过率: 50.0%）：
附加：无连接的block（通过: 3/4 | 通过率: 75.0%）：
"""}
    result = subject.extract_rationality_metrics(record)["metrics"]
    assert result["pages_observed"] == 2
    assert result["dimension_success_rates"][0]["passed"] == 3
    assert result["dimension_success_rates"][0]["total"] == 6
    assert result["dimension_success_rates"][1]["success_rate"] == 83.33


def test_structured_report_does_not_display_hallucinated_judge_numbers(monkeypatch):
    monkeypatch.setattr(subject, "run_json_judge", lambda **kwargs: {
        "model": "judge-model", "result": {"quality_level": "poor", "metrics": {},
            "summary": "4页全部通过，成功率22%", "issues": [{"message": "所有图页合格"}]}})
    record = {"sessionId": "session-lint", "checkType": "hscope_diagram_lint",
              "resultText": "## 图页：甲 — 发现问题\n检查1：器件标识（通过: 1/450 | 通过率: 0.22%）：\n"}
    result = subject.judge_rationality_result(record)
    assert "0.22%" in result["summary"]
    assert "4页全部通过" not in result["summary"]
    assert result["judge_unverified_summary"] == "4页全部通过，成功率22%"
    assert result["metrics"]["dimension_success_rates"][0]["success_rate"] == 0.22
