from agent_eval.llm_judge import _json_object
from agent_eval.scoring import (
    calculate_rule_dimensions,
    collect_process_metrics,
    combine_dimensions,
)


def test_collects_normalized_tool_token_context_and_subagent_metrics():
    results = [{
        "case_results": [{
            "duration_ms": 120,
            "final_message": "done",
            "transcript": [
                {"role": "assistant", "content": "working"},
                {"role": "tool_call", "tool_call": {"id": "1", "name": "spawn_agent"}},
                {"role": "tool_result", "tool_result": {"call_id": "1", "status": "completed"}},
                {"role": "assistant", "content": "AGENT_EVAL_TELEMETRY_JSON:{\"input_tokens\":10,\"output_tokens\":5,\"cache_read_tokens\":2,\"models\":[\"tested-model\"]}"},
            ],
        }]
    }]
    trace = {
        "model_call_count": 1, "model_call_success_rate": 100,
        "prompt_tokens": 12, "completion_tokens": 5,
        "max_prompt_tokens": 12, "models": ["tested-model"],
    }

    metrics = collect_process_metrics(results, trace)

    assert metrics["tool_calls"] == 1
    assert metrics["tool_completion_rate"] == 100
    assert metrics["subagent_calls"] == 1
    assert metrics["input_tokens"] == 12
    assert metrics["max_context_tokens"] == 12
    assert metrics["observed_models"] == ["tested-model"]
    assert metrics["final_output_present"] is True


def test_three_dimension_scoring_falls_back_to_rules_when_judge_is_unavailable():
    config = {
        "dimensions": {
            "result": {"weight": 0.5, "rule_weight": 0.6, "llm_weight": 0.4},
            "process": {"weight": 0.3, "rule_weight": 0.6, "llm_weight": 0.4},
            "skill_quality": {"weight": 0.2, "rule_weight": 0.6, "llm_weight": 0.4},
        },
        "process_rules": {},
    }
    rules = calculate_rule_dimensions(
        scores={"task_score": 100, "skill_gain": 100, "execution_stability": 100},
        process={"model_call_success_rate": 100, "tool_completion_rate": 100, "error_event_count": 0},
        skill_quality={"score": 80, "details": []},
        config=config,
    )
    report = combine_dimensions(
        rule_dimensions=rules,
        llm_judge={"status": "unavailable"},
        config=config,
    )

    assert report["dimensions"]["result"]["score"] == 100
    assert report["dimensions"]["skill_quality"]["score"] == 80
    assert report["overall_score"] == 96


def test_llm_judge_json_scores_are_clamped():
    parsed = _json_object(
        '{"dimensions":{"result":{"score":101},"process":{"score":50},'
        '"skill_quality":{"score":-1}},"risks":[]}'
    )
    assert parsed["dimensions"]["result"]["score"] == 100
    assert parsed["dimensions"]["skill_quality"]["score"] == 0
