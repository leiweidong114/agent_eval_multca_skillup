from __future__ import annotations

import http.client
import json
import threading
import time
import uuid
from collections import Counter
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
HOP_BY_HOP_HEADERS = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
     "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"}
)


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


class CodeBuddyCompatibilityProxy:
    """Per-run LiteLLM proxy with retries and optional CodeBuddy adaptation."""

    def __init__(
        self,
        upstream_url: str,
        *,
        timeout: int = 1800,
        max_attempts: int = 4,
        backoff_seconds: float = 0.5,
        forced_model: str | None = None,
        strip_tools_after_result: bool = True,
    ) -> None:
        self.upstream_url = upstream_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.forced_model = forced_model
        self.strip_tools_after_result = strip_tools_after_result
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._request_count = 0
        self._attempt_count = 0
        self._retry_count = 0
        self._status_counts: Counter[int] = Counter()
        self._transport_errors = 0

    @property
    def api_root(self) -> str:
        if self._server is None:
            raise RuntimeError("LiteLLM resilience proxy is not running")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def openai_base_url(self) -> str:
        return f"{self.api_root}/v1"

    @property
    def anthropic_base_url(self) -> str:
        return self.api_root

    @property
    def url(self) -> str:
        return f"{self.openai_base_url}/chat/completions"

    def stats(self) -> dict[str, object]:
        with self._lock:
            return {
                "request_count": self._request_count,
                "attempt_count": self._attempt_count,
                "retry_count": self._retry_count,
                "transport_errors": self._transport_errors,
                "status_counts": {str(k): v for k, v in sorted(self._status_counts.items())},
                "max_attempts": self.max_attempts,
            }

    def _record(self, *, request: bool = False, retry: bool = False,
                status: int | None = None, transport_error: bool = False) -> None:
        with self._lock:
            self._attempt_count += 1
            if request:
                self._request_count += 1
            if retry:
                self._retry_count += 1
            if status is not None:
                self._status_counts[status] += 1
            if transport_error:
                self._transport_errors += 1

    def start(self) -> None:
        if self._server is not None:
            return
        upstream = urlsplit(self.upstream_url)
        if upstream.scheme not in {"http", "https"} or not upstream.hostname:
            raise ValueError("LiteLLM upstream must be an absolute HTTP(S) URL")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or "0")
                body = self.rfile.read(length)
                try:
                    payload = json.loads(body)
                    if owner.forced_model and isinstance(payload, dict) and "model" in payload:
                        payload["model"] = owner.forced_model
                    messages = payload.get("messages") or [] if isinstance(payload, dict) else []
                    if (
                        owner.strip_tools_after_result
                        and isinstance(payload, dict)
                        and any(item.get("role") == "tool" for item in messages if isinstance(item, dict))
                    ):
                        payload.pop("tools", None)
                        payload.pop("tool_choice", None)
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                except (json.JSONDecodeError, AttributeError, TypeError):
                    pass

                incoming_path = urlsplit(self.path)
                relative = incoming_path.path[3:] if incoming_path.path.startswith("/v1") else incoming_path.path
                if upstream.path.endswith(("/chat/completions", "/messages")):
                    upstream_path = upstream.path
                else:
                    upstream_path = upstream.path.rstrip("/") + "/" + relative.lstrip("/")
                if incoming_path.query:
                    upstream_path += f"?{incoming_path.query}"
                headers = {
                    key: value for key, value in self.headers.items()
                    if key.lower() not in HOP_BY_HOP_HEADERS
                }
                headers["Content-Length"] = str(len(body))
                headers.setdefault("Idempotency-Key", f"agent-eval-{uuid.uuid4().hex}")

                last_error: Exception | None = None
                for attempt in range(owner.max_attempts):
                    connection_type = (
                        http.client.HTTPSConnection if upstream.scheme == "https"
                        else http.client.HTTPConnection
                    )
                    connection = connection_type(upstream.hostname, upstream.port, timeout=owner.timeout)
                    try:
                        connection.request("POST", upstream_path, body=body, headers=headers)
                        response = connection.getresponse()
                        response_body = response.read()
                        response_headers = list(response.getheaders())
                        retryable = response.status in RETRYABLE_STATUS_CODES
                        owner._record(
                            request=attempt == 0,
                            retry=retryable and attempt + 1 < owner.max_attempts,
                            status=response.status,
                        )
                        if retryable and attempt + 1 < owner.max_attempts:
                            delay = _retry_after(response.getheader("Retry-After"))
                            if delay is None:
                                delay = min(8.0, owner.backoff_seconds * (2 ** attempt))
                            if delay:
                                time.sleep(delay)
                            continue
                        self.send_response(response.status)
                        for key, value in response_headers:
                            if key.lower() not in HOP_BY_HOP_HEADERS:
                                self.send_header(key, value)
                        self.send_header("Content-Length", str(len(response_body)))
                        self.send_header("X-Agent-Eval-Attempts", str(attempt + 1))
                        self.end_headers()
                        self.wfile.write(response_body)
                        return
                    except (OSError, http.client.HTTPException) as exc:
                        last_error = exc
                        will_retry = attempt + 1 < owner.max_attempts
                        owner._record(request=attempt == 0, retry=will_retry, transport_error=True)
                        if will_retry:
                            delay = min(8.0, owner.backoff_seconds * (2 ** attempt))
                            if delay:
                                time.sleep(delay)
                            continue
                    finally:
                        connection.close()

                response_body = json.dumps(
                    {"error": {"type": "gateway_transport_error", "message": str(last_error)}}
                ).encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_body)))
                self.send_header("X-Agent-Eval-Attempts", str(owner.max_attempts))
                self.end_headers()
                self.wfile.write(response_body)

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
