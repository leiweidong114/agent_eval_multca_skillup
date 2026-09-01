from __future__ import annotations

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class CodeBuddyCompatibilityProxy:
    """Adapt CodeBuddy tool continuations for OpenAI-compatible gateways."""

    def __init__(self, upstream_url: str, *, timeout: int = 1800) -> None:
        self.upstream_url = upstream_url
        self.timeout = timeout
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("CodeBuddy compatibility proxy is not running")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/chat/completions"

    def start(self) -> None:
        if self._server is not None:
            return
        upstream = urlsplit(self.upstream_url)
        if upstream.scheme not in {"http", "https"} or not upstream.hostname:
            raise ValueError("CodeBuddy upstream must be an absolute HTTP(S) URL")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or "0")
                body = self.rfile.read(length)
                try:
                    payload = json.loads(body)
                    messages = payload.get("messages") or []
                    if any(item.get("role") == "tool" for item in messages if isinstance(item, dict)):
                        # MiniMax through this LiteLLM route rejects a second
                        # tool-calling request after a tool result. At that
                        # point CodeBuddy only needs a final answer, so omit
                        # the reusable tool schemas from the continuation.
                        payload.pop("tools", None)
                        payload.pop("tool_choice", None)
                        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                except (json.JSONDecodeError, AttributeError):
                    pass

                connection_type = (
                    http.client.HTTPSConnection if upstream.scheme == "https"
                    else http.client.HTTPConnection
                )
                connection = connection_type(upstream.hostname, upstream.port, timeout=owner.timeout)
                headers = {
                    "Content-Type": self.headers.get("Content-Type", "application/json"),
                    "Accept": self.headers.get("Accept", "*/*"),
                }
                authorization = self.headers.get("Authorization")
                if authorization:
                    headers["Authorization"] = authorization
                try:
                    path = upstream.path or "/"
                    if upstream.query:
                        path += f"?{upstream.query}"
                    connection.request("POST", path, body=body, headers=headers)
                    response = connection.getresponse()
                    response_body = response.read()
                    self.send_response(response.status)
                    self.send_header(
                        "Content-Type", response.getheader("Content-Type", "application/json")
                    )
                    self.send_header("Content-Length", str(len(response_body)))
                    self.end_headers()
                    self.wfile.write(response_body)
                except Exception as exc:
                    response_body = json.dumps(
                        {"error": {"message": f"CodeBuddy gateway proxy failed: {exc}"}}
                    ).encode("utf-8")
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response_body)))
                    self.end_headers()
                    self.wfile.write(response_body)
                finally:
                    connection.close()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> "CodeBuddyCompatibilityProxy":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
