from agent_eval.llm_judge import _json_object
from agent_eval.scoring import (
    calculate_rule_dimensions,
    collect_process_metrics,
    collect_skill_read_evidence,
    combine_dimensions,
    supplement_database_tool_metrics,
)


def test_skill_read_evidence_requires_read_tool_and_skill_md_path():
    interactions = [{
        "request_id": "req-1",
        "response": {"choices": [{"message": {"tool_calls": [
            {"function": {"name": "read", "arguments": '{"path":"skills/schematic-pipeline/SKILL.md"}'}},
        ]}}]},
    }]
    result = collect_skill_read_evidence(
        interactions, ["schematic-pipeline", "schematic-web-apply"]
    )
    assert result["status"] == "partial"
    assert result["observed_skills"] == ["schematic-pipeline"]
    assert result["missing_skills"] == ["schematic-web-apply"]


def test_skill_read_evidence_accepts_powershell_get_content():
    interactions = [{
        "request_id": "req-1",
        "response": {"choices": [{"message": {"tool_calls": [{
            "function": {"name": "shell_command", "arguments":
                         '{"command":"Get-Content skills/02-signal-interface-generation/SKILL.md"}'},
        }]}}]},
    }]
    result = collect_skill_read_evidence(interactions, ["signal-interface-generation"])
    assert result["status"] == "verified"


def test_skill_usage_evidence_accepts_bundled_script_execution():
    interactions = [{
        "request_id": "req-apply",
        "response": {"choices": [{"message": {"tool_calls": [{
            "function": {"name": "shell_command", "arguments":
                         '{"command":"python .agents/skills/bundle/skills/04-schematic-web-apply/scripts/apply.py --layout-dir out/layout"}'},
        }]}}]},
    }]
    result = collect_skill_read_evidence(interactions, ["schematic-web-apply"])
    assert result["status"] == "verified"
    assert result["all_selected_skills_observed"] is True
    assert result["all_selected_skills_read"] is False
    assert result["evidence"]["schematic-web-apply"][0]["kind"] == "bundled_script_execution"


def test_database_supplements_native_subagent_when_transcript_has_shell_tools():
    process = {
        "tool_calls": 2, "tool_results": 2, "tool_failures": 0,
        "subagent_calls": 0, "subagent_attempts": 0, "subagent_failures": 0,
    }
    interactions = [
        {"response": {"choices": [{"message": {"tool_calls": [{
            "id": "spawn-1", "function": {
                "name": "multi_agent_v1__spawn_agent", "arguments": "{}",
            },
        }]}}]}},
        {"proxy_server_request": {"messages": [{
            "role": "tool", "tool_call_id": "spawn-1",
            "content": '{"agent_id":"child-1"}',
        }]}},
    ]
    supplement_database_tool_metrics(process, interactions)
    assert process["tool_calls"] == 2
    assert process["subagent_calls"] == 1
    assert process["subagent_attempts"] == 1
    assert process["subagent_tool_names"] == ["multi_agent_v1__spawn_agent"]
    assert process["tool_event_source"] == "agent_transcript+litellm_subagent_supplement"


def test_collects_normalized_tool_token_context_and_subagent_metrics():
    results = [{
        "case_results": [{
            "duration_ms": 120,
            "final_message": "done",
            "transcript": [
                {"role": "assistant", "content": "working"},
                {"role": "tool_call", "tool_call": {"id": "1", "name": "sessions_spawn"}},
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
    assert metrics["subagent_attempts"] == 1
    assert metrics["subagent_failures"] == 0
    assert metrics["input_tokens"] == 12
    assert metrics["max_context_tokens"] == 12
    assert metrics["observed_models"] == ["tested-model"]
    assert metrics["final_output_present"] is True


def test_failed_subagent_attempt_is_not_counted_as_a_success():
    results = [{"case_results": [{"transcript": [
        {"role": "tool_call", "tool_call": {"id": "spawn-1", "name": "sessions_spawn"}},
        {"role": "tool_result", "tool_result": {"call_id": "spawn-1", "status": "error"}},
    ]}]}]

    metrics = collect_process_metrics(results, {})

    assert metrics["subagent_calls"] == 0
    assert metrics["subagent_attempts"] == 1
    assert metrics["subagent_failures"] == 1


def test_openclaw_exec_child_is_counted_but_ordinary_exec_is_not():
    results = [{"case_results": [{"transcript": [
        {"role": "tool_call", "tool_call": {
            "id": "child-1", "name": "exec",
            "arguments": {"command": "openclaw agent exec --json child"},
        }},
        {"role": "tool_result", "tool_result": {"call_id": "child-1", "status": "completed"}},
        {"role": "tool_call", "tool_call": {
            "id": "ordinary-1", "name": "exec",
            "arguments": {"command": "python build.py"},
        }},
        {"role": "tool_result", "tool_result": {"call_id": "ordinary-1", "status": "completed"}},
    ]}]}]

    metrics = collect_process_metrics(results, {})

    assert metrics["tool_calls"] == 2
    assert metrics["subagent_calls"] == 1
    assert metrics["subagent_attempts"] == 1


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
