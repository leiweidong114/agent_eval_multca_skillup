import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from agent_eval.codebuddy_proxy import CodeBuddyCompatibilityProxy


def test_proxy_strips_tools_only_after_a_tool_result():
    received: list[dict[str, object]] = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(json.loads(body))
            response = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    host, port = upstream.server_address[:2]
    try:
        with CodeBuddyCompatibilityProxy(f"http://{host}:{port}/v1/chat/completions", strip_tools_after_result=True) as proxy:
            payload = {
                "model": "opencode-go/minimax-m2.7",
                "messages": [{"role": "tool", "tool_call_id": "call_1", "content": "ok"}],
                "tools": [{"type": "function", "function": {"name": "Skill"}}],
                "tool_choice": "auto",
            }
            request = Request(
                proxy.url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                assert response.status == 200
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    assert received[0]["model"] == "opencode-go/minimax-m2.7"
    assert "tools" not in received[0]
    assert "tool_choice" not in received[0]


def test_proxy_retries_429_and_forces_the_gateway_model():
    received: list[dict[str, object]] = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(json.loads(body))
            if len(received) == 1:
                response = b'{"error":"busy"}'
                self.send_response(429)
                self.send_header("Retry-After", "0")
            else:
                response = b'{"ok":true}'
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    host, port = upstream.server_address[:2]
    try:
        with CodeBuddyCompatibilityProxy(
            f"http://{host}:{port}/v1",
            forced_model="opencode-go/minimax-m2.7",
            backoff_seconds=0,
        ) as proxy:
            request = Request(
                proxy.url,
                data=json.dumps({"model": "custom-local:MiniMax-M2.7", "messages": []}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                assert response.status == 200
            stats = proxy.stats()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    assert len(received) == 2
    assert all(item["model"] == "opencode-go/minimax-m2.7" for item in received)
    assert stats["retry_count"] == 1
    assert stats["status_counts"] == {"200": 1, "429": 1}
    assert stats["last_failure"] is None


def test_proxy_does_not_retry_permanent_weekly_usage_limit():
    calls = 0

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            nonlocal calls
            calls += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            response = b'{"error":{"type":"GoUsageLimitError","message":"Weekly usage limit reached"}}'
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    host, port = upstream.server_address[:2]
    try:
        with CodeBuddyCompatibilityProxy(
            f"http://{host}:{port}/v1", max_attempts=4, backoff_seconds=0
        ) as proxy:
            request = Request(
                proxy.url,
                data=json.dumps({"model": "opencode-go/test", "messages": []}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(request)
            except Exception as exc:
                assert getattr(exc, "code", None) == 429
            stats = proxy.stats()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    assert calls == 1
    assert stats["retry_count"] == 0
    assert stats["attempt_count"] == 1


def test_proxy_does_not_retry_permanent_chinese_balance_limit():
    calls = 0

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            nonlocal calls
            calls += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            response = json.dumps(
                {"error": {"message": "余额不足或无可用资源包,请充值。"}},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    host, port = upstream.server_address[:2]
    try:
        with CodeBuddyCompatibilityProxy(
            f"http://{host}:{port}/v1", max_attempts=4, backoff_seconds=0
        ) as proxy:
            request = Request(
                proxy.url,
                data=json.dumps({"model": "glm-4.7", "messages": []}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(request)
            except Exception as exc:
                assert getattr(exc, "code", None) == 429
            stats = proxy.stats()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    assert calls == 1
    assert stats["retry_count"] == 0
    assert stats["attempt_count"] == 1
    assert stats["last_failure"]["status_code"] == 429
    assert "余额不足或无可用资源包" in stats["last_failure"]["detail"]


def test_proxy_redacts_credentials_from_recorded_failure():
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            response = b'{"error":{"message":"Bearer sk-secret-value rejected"}}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    host, port = upstream.server_address[:2]
    try:
        with CodeBuddyCompatibilityProxy(f"http://{host}:{port}/v1") as proxy:
            request = Request(
                proxy.url,
                data=json.dumps({"model": "test", "messages": []}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(request)
            except Exception as exc:
                assert getattr(exc, "code", None) == 401
            stats = proxy.stats()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    assert "secret-value" not in stats["last_failure"]["detail"]
    assert "[REDACTED]" in stats["last_failure"]["detail"]


def test_proxy_restores_client_model_in_anthropic_response():
    received: list[dict[str, object]] = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(json.loads(body))
            response = json.dumps({"type": "message", "model": "glm-4.7-anthropic"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    host, port = upstream.server_address[:2]
    try:
        with CodeBuddyCompatibilityProxy(
            f"http://{host}:{port}", forced_model="glm-4.7-anthropic"
        ) as proxy:
            request = Request(
                f"{proxy.anthropic_base_url}/v1/messages",
                data=json.dumps({"model": "sonnet", "messages": []}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                payload = json.loads(response.read())
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    assert received[0]["model"] == "glm-4.7-anthropic"
    assert payload["model"] == "sonnet"


def test_proxy_adds_internal_headers_and_preserves_agent_metadata():
    received: list[tuple[dict[str, str], dict[str, object]]] = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(({key.lower(): value for key, value in self.headers.items()}, body))
            response = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    host, port = upstream.server_address[:2]
    try:
        with CodeBuddyCompatibilityProxy(
            f"http://{host}:{port}/v1",
            upstream_headers={
                "User-Agent": "OpenAI/Python", "x-cookie": "11", "x-user-account": "E123"
            },
            request_metadata={"agent_eval_task_id": "task-1"},
        ) as proxy:
            request = Request(
                proxy.url,
                data=json.dumps({
                    "model": "m", "messages": [],
                    "metadata": {"session_id": "child", "parent_session_id": "parent"},
                }).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urlopen(request) as response:
                assert response.status == 200
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    headers, body = received[0]
    assert headers["user-agent"] == "OpenAI/Python"
    assert headers["x-user-account"] == "E123"
    assert body["metadata"] == {
        "agent_eval_task_id": "task-1", "session_id": "child", "parent_session_id": "parent"
    }
