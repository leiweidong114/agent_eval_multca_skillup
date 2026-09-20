from __future__ import annotations

from app.session_task_classifier import classify_session_task, first_user_prompt


def _conversation() -> dict:
    return {
        "root_session_id": "session-1",
        "timeline": [
            {
                "request_id": "r1",
                "start_time": "2026-09-20T00:00:00Z",
                "proxy_server_request": {
                    "body": {
                        "messages": [
                            {"role": "system", "content": "system"},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "根据这份信号接口列表生成原理图"}
                                ],
                            },
                        ]
                    }
                },
            }
        ],
    }


def test_first_user_prompt_reads_earliest_request() -> None:
    conversation = _conversation()
    conversation["timeline"].append(
        {
            "request_id": "r2",
            "start_time": "2026-09-20T00:01:00Z",
            "messages": [{"role": "user", "content": "后续提示词"}],
        }
    )
    assert first_user_prompt(conversation) == "根据这份信号接口列表生成原理图"


def test_classifier_normalizes_generation_hierarchy(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.session_task_classifier.run_json_judge",
        lambda **kwargs: {
            "model": "judge-model",
            "usage": {"total_tokens": 12},
            "result": {
                "task_type": "signal_list_to_schematic",
                "confidence": 0.94,
                "reason": "用户提供信号接口列表并要求生成原理图",
            },
        },
    )
    result = classify_session_task(_conversation(), employee_no="100001")
    assert result["status"] == "completed"
    assert result["task_category"] == "schematic_generation"
    assert result["task_subtype"] == "signal_list_to_schematic"
    assert result["confidence"] == 0.94


def test_classifier_rejects_unknown_type(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.session_task_classifier.run_json_judge",
        lambda **kwargs: {"result": {"task_type": "invented_type"}},
    )
    result = classify_session_task(_conversation())
    assert result["status"] == "unavailable"
    assert "Unsupported task_type" in result["error"]
