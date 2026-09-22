import json

from app.quality_summary import summarize_quality_records


def test_all_four_types_and_repeated_records_are_summarized():
    rows = [
        {"_id": "lint-1", "checkType": "hscope_diagram_lint", "resultText": """## 图页：甲 — 检查
检查1：器件标识（通过: 1/2 | 通过率: 50%）：
检查2：端口（通过: 2/2 | 通过率: 100%）：
"""},
        {"_id": "lint-2", "checkType": "hscope_diagram_lint", "resultText": """## 图页：乙 — 检查
检查1：器件标识（通过: 2/2 | 通过率: 100%）：
检查2：端口（通过: 1/2 | 通过率: 50%）：
"""},
        {"_id": "corpus", "checkType": "hscope_block_corpus_check  ",
         "resultText": "block 条目总数: 12\n语料库有数据: 9\n语料库无数据: 3\n语料库覆盖率: 75.0%"},
        {"_id": "signal", "checkType": "signal-interface-checker",
         "resultText": json.dumps({"检查通过率": 88})},
        {"_id": "drc", "checkType": "tianshu-drc-review", "resultText": "DRC审查通过率：91.5%"},
        {"_id": "summary", "checkType": "agent_eval_session_metrics", "resultText": "{}"},
    ]
    summary = summarize_quality_records("session-1", rows)
    assert summary["source_record_count"] == 5
    lint = summary["by_check_type"]["hscope_diagram_lint"]
    assert lint["record_count"] == 2
    assert lint["rates"] == {"器件标识": 75.0, "端口": 75.0, "overall_pass_rate": 75.0}
    assert summary["rates"]["hscope_block_corpus_check__coverage_rate"] == 75.0
    assert summary["rates"]["signal-interface-checker__pass_rate"] == 88.0
    assert summary["rates"]["tianshu-drc-review__drc_pass_rate"] == 91.5


def test_rate_only_reports_without_denominators_are_not_averaged():
    rows = [{"checkType": "signal-interface-checker", "resultText": '{"passRate": 80}'},
            {"checkType": "signal-interface-checker", "resultText": '{"passRate": 90}'}]
    summary = summarize_quality_records("session-2", rows)
    assert summary["by_check_type"]["signal-interface-checker"]["rates"]["pass_rate"] is None
