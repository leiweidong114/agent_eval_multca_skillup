import json
import pytest
from agent_eval.protocol_adapter import to_chat, from_chat


@pytest.mark.parametrize('protocol', ['messages', 'responses'])
def test_text_and_tool_round_trip_preserves_arguments(protocol):
    upstream = {'choices': [{'finish_reason': 'tool_calls', 'message': {'content': None,
        'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"中文.txt"}'}}]}}],
        'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}}
    body, content_type = from_chat(upstream, protocol, 'glm-4.5-air', False)
    result = json.loads(body)
    if protocol == 'responses':
        source = {'model': 'glm-4.5-air', 'input': result['output'] + [{'type': 'function_call_output', 'call_id': 'call_1', 'output': 'file content'}]}
    else:
        source = {'model': 'glm-4.5-air', 'messages': [{'role': 'assistant', 'content': result['content']},
                  {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call_1', 'content': 'file content'}]}]}
    chat = to_chat(source, protocol)
    assert json.loads(chat['messages'][0]['tool_calls'][0]['function']['arguments']) == {'path': '中文.txt'}
    assert chat['messages'][1] == {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'file content'}
    stream, mime = from_chat(upstream, protocol, 'glm-4.5-air', True)
    assert mime == 'text/event-stream'
    assert b'call_1' in stream


def test_unsupported_modalities_fail_instead_of_silent_loss():
    with pytest.raises(ValueError, match='modality'):
        to_chat({'model': 'a', 'input': [{'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'test'}]}]}, 'responses')


def test_wrong_model_group_is_not_accepted_by_leaf_name():
    from agent_eval.database import verify_requested_model
    assert not verify_requested_model([{'model': 'vendor/model', 'model_group': 'other/model', 'status': 'success'}],
                                      expected_model='wanted/model', exact=True)['verified']


def test_codex_namespace_custom_tool_roundtrip():
    request = {'model': 'glm', 'input': 'HI', 'tools': [{'type': 'namespace', 'name': 'functions',
        'tools': [{'type': 'custom', 'name': 'apply_patch', 'description': 'patch'}]}]}
    chat = to_chat(request, 'responses')
    assert chat['tools'][0]['function']['name'] == 'functions__apply_patch'
    upstream = {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
        {'id': 't', 'function': {'name': 'functions__apply_patch', 'arguments': '{"input":"patch data"}'}}]}}]}
    body, _ = from_chat(upstream, 'responses', 'glm', False, request)
    item = json.loads(body)['output'][0]
    assert item['type'] == 'custom_tool_call'
    assert item['namespace'] == 'functions'
    assert item['name'] == 'apply_patch'
    assert item['input'] == 'patch data'
    request['input'] = [item, {'type': 'custom_tool_call_output', 'call_id': 't', 'output': 'ok'}]
    assert to_chat(request, 'responses')['messages'][0]['tool_calls'][0]['function']['name'] == 'functions__apply_patch'


def test_codex_subagent_additional_tools_are_merged_and_roundtrip():
    request = {
        'model': 'glm',
        'input': [
            {'role': 'user', 'content': [{'type': 'input_text', 'text': 'delegate'}]},
            {
                'type': 'additional_tools',
                'id': 'subagent-tools',
                'role': 'system',
                'tools': [
                    {'type': 'namespace', 'name': 'functions', 'tools': [
                        {'type': 'custom', 'name': 'apply_patch', 'description': 'patch'},
                    ]},
                ],
            },
        ],
        'tools': [{'type': 'function', 'name': 'read_file', 'parameters': {'type': 'object'}}],
    }

    chat = to_chat(request, 'responses')
    assert chat['messages'] == [{'role': 'user', 'content': 'delegate'}]
    assert [item['function']['name'] for item in chat['tools']] == [
        'read_file', 'functions__apply_patch',
    ]

    upstream = {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
        {'id': 'sub-call', 'function': {
            'name': 'functions__apply_patch', 'arguments': '{"input":"patch data"}',
        }},
    ]}}]}
    body, _ = from_chat(upstream, 'responses', 'glm', False, request)
    item = json.loads(body)['output'][0]
    assert item['type'] == 'custom_tool_call'
    assert item['namespace'] == 'functions'
    assert item['name'] == 'apply_patch'
    assert item['input'] == 'patch data'


def test_codex_additional_tools_requires_a_list():
    with pytest.raises(ValueError, match='must be a list'):
        to_chat({
            'model': 'glm',
            'input': [{'type': 'additional_tools', 'tools': {'type': 'function'}}],
        }, 'responses')


def test_gateway_tool_fallback_deduplicates_history_and_does_not_invent_results():
    from agent_eval.scoring import supplement_database_tool_metrics
    row = {'proxy_server_request': {'body': {'messages': [
        {'role': 'assistant', 'tool_calls': [{'id': 'a', 'function': {'name': 'read'}}, {'id': 'b', 'function': {'name': 'write'}}]},
        {'role': 'tool', 'tool_call_id': 'a', 'content': 'read result'}]}}}
    metrics = {'tool_calls': 0}
    supplement_database_tool_metrics(metrics, [row, row])
    assert metrics['tool_calls'] == 2
    assert metrics['tool_results'] == 1
    assert metrics['tool_completion_rate'] == 50
    assert metrics['tool_failures'] == 0
    assert metrics['tool_failure_measurement'] == 'tool_result_content'


def test_gateway_tool_fallback_correlates_openclaw_sanitized_ids():
    from agent_eval.scoring import supplement_database_tool_metrics
    rows = [{'response': {'choices': [{'message': {'tool_calls': [{'id': 'call_abc', 'function': {'name': 'read'}}]}}]}},
            {'proxy_server_request': {'messages': [{'role': 'assistant', 'tool_calls': [{'id': 'callabc', 'function': {'name': 'read'}}]},
                                                   {'role': 'tool', 'tool_call_id': 'callabc', 'content': 'ok'}]}}]
    metrics = {'tool_calls': 0}
    supplement_database_tool_metrics(metrics, rows)
    assert metrics['tool_calls'] == 1
    assert metrics['tool_results'] == 1
    assert metrics['tool_completion_rate'] == 100
