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
        with CodeBuddyCompatibilityProxy(f"http://{host}:{port}/v1/chat/completions") as proxy:
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
