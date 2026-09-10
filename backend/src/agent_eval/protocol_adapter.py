"""Stateless text/function-tool protocol adaptation to Chat Completions.

Unsupported modalities fail explicitly instead of silently discarding inputs.
The upstream still performs all inference; this module only translates envelopes.
"""
import html
import json
import re
import time
import uuid


_TAGGED_TOOL_CALL = re.compile(
    r"(?:</think>\s*)?(?:<think>|<tool_call>)\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)\s*"
    r"(?P<body>.*?)</tool_call>",
    re.DOTALL,
)
_TAGGED_TOOL_ARGUMENT = re.compile(
    r"<arg_key>\s*(?P<key>.*?)\s*</arg_key>\s*"
    r"<arg_value>(?P<value>.*?)</arg_value>",
    re.DOTALL,
)


def flatten_tools(tools, namespace=None):
    for tool in tools:
        if tool.get('type') == 'namespace':
            yield from flatten_tools(tool.get('tools', []), tool['name'])
        else:
            yield {**tool, **({'namespace': namespace} if namespace else {})}


def response_tools(payload):
    """Return top-level and turn-scoped Responses tools in wire order.

    Codex uses an ``additional_tools`` input item when a child agent is
    started.  Those tools are scoped to the child turn, but Chat Completions
    has only one top-level ``tools`` collection, so the compatibility proxy
    must merge both locations before forwarding the request.
    """
    tools = list(payload.get("tools") or [])
    source = payload.get("input") or []
    if isinstance(source, list):
        for item in source:
            if isinstance(item, dict) and item.get("type") == "additional_tools":
                additional = item.get("tools") or []
                if not isinstance(additional, list):
                    raise ValueError("Responses additional_tools.tools must be a list")
                tools.extend(additional)
    return tools


def wire_name(item):
    return (item['namespace'] + '__' if item.get('namespace') else '') + item['name']


def text_blocks(content):
    if isinstance(content, str) or content is None:
        return content or ""
    texts = []
    for block in content:
        if block.get("type") not in {"text", "input_text", "output_text"}:
            raise ValueError(f"Unsupported content modality: {block.get('type')}")
        texts.append(block.get("text", ""))
    return "\n".join(texts)


def recover_tagged_tool_call(message, request):
    """Recover a tool call emitted as tagged text by OpenAI-compatible models.

    Some chat models occasionally serialize an otherwise unambiguous Codex tool
    call as ``<think>tool_name <arg_key>...`` text and finish with ``stop``.
    Only convert the strict tagged shape when the named tool was actually offered
    in this request.  Ordinary text and unknown tool names remain untouched.
    """
    if message.get("tool_calls") or not isinstance(message.get("content"), str):
        return message, False
    matches = list(_TAGGED_TOOL_CALL.finditer(message["content"]))
    if not matches:
        return message, False
    match = matches[-1]
    available = {wire_name(tool) for tool in flatten_tools(response_tools(request or {}))}
    name = match.group("name")
    if name not in available:
        return message, False
    arguments = {}
    for item in _TAGGED_TOOL_ARGUMENT.finditer(match.group("body")):
        key = html.unescape(item.group("key")).strip()
        if not key or key in arguments:
            return message, False
        arguments[key] = html.unescape(item.group("value")).strip()
    if not arguments:
        return message, False
    recovered = dict(message)
    recovered["content"] = None
    recovered["tool_calls"] = [{
        "id": "call_recovered_" + uuid.uuid4().hex,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }]
    return recovered, True


def to_chat(payload, protocol):
    messages = []
    system = payload.get("instructions") if protocol == "responses" else payload.get("system")
    if system:
        messages.append({"role": "system", "content": text_blocks(system)})
    source = payload.get("input", []) if protocol == "responses" else payload.get("messages", [])
    if isinstance(source, str):
        source = [{"role": "user", "content": source}]
    if payload.get("previous_response_id"):
        raise ValueError("Stateful previous_response_id is unsupported; send the full conversation")
    for item in source:
        kind = item.get("type")
        if protocol == "responses" and kind == "additional_tools":
            # Tool definitions are merged below.  This item is not a chat
            # message and must not be forwarded as user-visible content.
            continue
        if kind == "reasoning":
            continue  # Server-side reasoning tokens are not user/tool messages.
        if kind in {"function_call", "custom_tool_call"}:
            messages.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": item.get("call_id") or item.get("id"), "type": "function",
                "function": {"name": wire_name(item), "arguments": item.get("arguments") or json.dumps({'input': item.get('input', '')})}}]})
            continue
        if kind in {"function_call_output", "custom_tool_call_output"}:
            messages.append({"role": "tool", "tool_call_id": item["call_id"],
                             "content": text_blocks(item.get("output"))})
            continue
        content = item.get("content", "")
        if protocol == "messages" and isinstance(content, list):
            tools, results, texts = [], [], []
            for block in content:
                if block.get("type") == "tool_use":
                    tools.append({"id": block["id"], "type": "function", "function": {
                        "name": block["name"], "arguments": json.dumps(block.get("input", {}))}})
                elif block.get("type") == "tool_result":
                    results.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                                    "content": text_blocks(block.get("content"))})
                elif block.get("type") in {"thinking", "redacted_thinking"}:
                    continue
                else:
                    texts.append(text_blocks([block]))
            messages.extend(results)
            if tools or texts:
                msg = {"role": item["role"], "content": "\n".join(texts) or None}
                if tools:
                    msg["tool_calls"] = tools
                messages.append(msg)
        else:
            if kind not in {None, "message"}:
                raise ValueError(f"Unsupported Responses input item: {kind}")
            messages.append({"role": item.get("role", "user"), "content": text_blocks(content)})
    body = {"model": payload["model"], "messages": messages, "stream": False}
    tools = []
    tool_source = response_tools(payload) if protocol == "responses" else payload.get("tools") or []
    seen_tool_names = set()
    for tool in flatten_tools(tool_source):
        if protocol == "responses":
            if tool.get("type") not in {"function", "custom"}:
                raise ValueError(f"Unsupported Responses tool: {tool.get('type')}")
            function = {k: tool[k] for k in ("description", "parameters", "strict") if k in tool}
            function['name'] = wire_name(tool)
            if function['name'] in seen_tool_names:
                continue
            seen_tool_names.add(function['name'])
            if tool['type'] == 'custom':
                function['parameters'] = {'type': 'object', 'properties': {'input': {'type': 'string'}}, 'required': ['input']}
            tools.append({"type": "function", "function": function})
        else:
            if tool.get("type") and tool["type"] != "custom":
                raise ValueError(f"Unsupported Anthropic tool: {tool.get('type')}")
            tools.append({"type": "function", "function": {"name": tool["name"],
                          "description": tool.get("description", ""),
                          "parameters": tool.get("input_schema", {"type": "object", "properties": {}})}})
    if tools:
        body["tools"] = tools
    choice = payload.get("tool_choice")
    if isinstance(choice, dict):
        kind = choice.get("type")
        choice = ({"type": "function", "function": {"name": choice["name"]}}
                  if kind in {"tool", "function"} else {"auto": "auto", "any": "required", "none": "none"}.get(kind))
    if choice:
        body["tool_choice"] = choice
    for key in ("temperature", "top_p", "parallel_tool_calls"):
        if key in payload:
            body[key] = payload[key]
    # Leave reasoning enabled at the provider default; never send disable flags.
    if protocol == "messages" and payload.get("max_tokens"):
        body["max_tokens"] = payload["max_tokens"]
    if protocol == "responses" and payload.get("max_output_tokens"):
        body["max_tokens"] = payload["max_output_tokens"]
    return body


def from_chat(payload, protocol, model, stream, request=None):
    choice = payload["choices"][0]
    message, recovered_tool_call = recover_tagged_tool_call(choice["message"], request or {})
    if recovered_tool_call:
        choice = {**choice, "finish_reason": "tool_calls"}
    usage = payload.get("usage") or {}
    tokens = {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}
    events = []
    if protocol == "messages":
        blocks = []
        if message.get("content"):
            blocks.append({"type": "text", "text": message["content"]})
        for tool in message.get("tool_calls") or []:
            blocks.append({"type": "tool_use", "id": tool["id"], "name": tool["function"]["name"],
                           "input": json.loads(tool["function"]["arguments"])})
        stop = "tool_use" if message.get("tool_calls") else "max_tokens" if choice.get("finish_reason") == "length" else "end_turn"
        response = {"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant",
                    "model": model, "content": blocks, "stop_reason": stop, "stop_sequence": None, "usage": tokens}
        events.append(("message_start", {"message": {**response, "content": [], "stop_reason": None}}))
        for i, block in enumerate(blocks):
            empty = {**block, "text": ""} if block["type"] == "text" else {**block, "input": {}}
            events.append(("content_block_start", {"index": i, "content_block": empty}))
            delta = ({"type": "text_delta", "text": block["text"]} if block["type"] == "text"
                     else {"type": "input_json_delta", "partial_json": json.dumps(block["input"])})
            events.extend([("content_block_delta", {"index": i, "delta": delta}), ("content_block_stop", {"index": i})])
        events.extend([("message_delta", {"delta": {"stop_reason": stop, "stop_sequence": None}, "usage": tokens}), ("message_stop", {})])
    else:
        output = []
        if message.get("content"):
            output.append({"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant",
                           "status": "completed", "content": [{"type": "output_text", "text": message["content"], "annotations": []}]})
        for tool in message.get("tool_calls") or []:
            fn = tool['function']
            request_tools = response_tools(request or {})
            spec = next((t for t in flatten_tools(request_tools) if wire_name(t) == fn['name']), {})
            custom = spec.get('type') == 'custom'
            output.append({"id": "fc_" + uuid.uuid4().hex, "type": "custom_tool_call" if custom else "function_call", "status": "completed",
                           "call_id": tool["id"], 'name': spec.get('name', fn['name']),
                           **({'namespace': spec['namespace']} if spec.get('namespace') else {}),
                           **({'input': json.loads(fn['arguments'])['input']} if custom else {'arguments': fn['arguments']})})
        response = {"id": "resp_" + uuid.uuid4().hex, "object": "response", "created_at": int(time.time()),
                    "status": "incomplete" if choice.get("finish_reason") == "length" else "completed",
                    "error": None, "model": model, "output": output,
                    "usage": {**tokens, "total_tokens": usage.get("total_tokens", 0)}}
        events.append(("response.created", {"response": {**response, "status": "in_progress", "output": []}}))
        for i, item in enumerate(output):
            empty = {**item, "status": "in_progress"}
            empty.update({"content": []} if item["type"] == "message" else {"input" if item['type'] == 'custom_tool_call' else "arguments": ""})
            events.append(("response.output_item.added", {"output_index": i, "item": empty}))
            if item["type"] == "message":
                part = item["content"][0]
                base = {"item_id": item["id"], "output_index": i, "content_index": 0}
                events.extend([
                    ("response.content_part.added", {**base, "part": {**part, "text": ""}}),
                    ("response.output_text.delta", {**base, "delta": part["text"]}),
                    ("response.output_text.done", {**base, "text": part["text"]}),
                    ("response.content_part.done", {**base, "part": part})])
            else:
                base = {"item_id": item["id"], "output_index": i}
                field = 'input' if item['type'] == 'custom_tool_call' else 'arguments'
                event = 'custom_tool_call_input' if field == 'input' else 'function_call_arguments'
                events.extend([(f"response.{event}.delta", {**base, "delta": item[field]}),
                               (f"response.{event}.done", {**base, field: item[field]})])
            events.append(("response.output_item.done", {"output_index": i, "item": item}))
        events.append(("response.completed" if response["status"] == "completed" else "response.incomplete", {"response": response}))
    if not stream:
        return json.dumps(response, ensure_ascii=False).encode(), "application/json"
    return "".join(f"event: {name}\ndata: {json.dumps({'type': name, **data, **({'sequence_number': i} if protocol == 'responses' else {})}, ensure_ascii=False)}\n\n"
                   for i, (name, data) in enumerate(events)).encode(), "text/event-stream"
