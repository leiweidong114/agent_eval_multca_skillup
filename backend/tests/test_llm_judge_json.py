import pytest

from agent_eval.llm_judge import _generic_json_object, _judge_response_content


def test_generic_json_object_accepts_reasoning_and_markdown_wrappers():
    value = _generic_json_object(
        '<think>internal reasoning</think>\n```json\n{"status":"ok"}\n```'
    )

    assert value == {"status": "ok"}


def test_generic_json_object_extracts_object_from_explanatory_text():
    value = _generic_json_object('结果如下：\n{"status":"ok","score":1}\n完成。')

    assert value["status"] == "ok"
    assert value["score"] == 1


def test_empty_judge_content_has_actionable_error_and_response_shape():
    content, diagnostics = _judge_response_content({
        "choices": [{
            "finish_reason": "length",
            "message": {"content": "", "reasoning_content": "thinking"},
        }]
    })

    assert diagnostics == {
        "finish_reason": "length",
        "content_length": 0,
        "reasoning_content_length": 8,
    }
    with pytest.raises(ValueError, match="empty message.content"):
        _generic_json_object(content)
