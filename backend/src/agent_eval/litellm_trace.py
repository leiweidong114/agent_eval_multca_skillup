from __future__ import annotations

import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx


RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class TraceKeyError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def _post_with_retry(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, object],
    max_attempts: int = 4,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=15)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt + 1 == max_attempts:
                raise TraceKeyError(
                    f"LiteLLM trace-key endpoint is unreachable after {max_attempts} attempts",
                    retryable=True,
                ) from exc
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                if response.is_error:
                    raise TraceKeyError(
                        f"LiteLLM trace-key endpoint returned HTTP {response.status_code}",
                        retryable=False,
                        status_code=response.status_code,
                    )
                return response
            last_error = TraceKeyError(
                f"LiteLLM trace-key endpoint returned HTTP {response.status_code}",
                retryable=True,
                status_code=response.status_code,
            )
            if attempt + 1 == max_attempts:
                raise last_error
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after is not None else 0.5 * (2 ** attempt)
            except ValueError:
                delay = 0.5 * (2 ** attempt)
            time.sleep(min(8.0, max(0.0, delay)))
            continue
        if attempt + 1 < max_attempts:
            time.sleep(min(8.0, 0.5 * (2 ** attempt)))
    raise TraceKeyError(f"LiteLLM trace-key request failed: {last_error}", retryable=True)


@dataclass(frozen=True)
class TraceKey:
    key: str
    alias: str
    api_root: str
    master_key: str


def create_trace_key(
    api_base: str,
    model: str,
    run_id: str,
    *,
    master_key: str | None = None,
) -> TraceKey | None:
    master_key = (master_key or os.environ.get("LITELLM_MASTER_KEY", "")).strip()
    if not master_key or not api_base:
        return None
    parsed = urlsplit(api_base.rstrip("/"))
    path = parsed.path[:-3] if parsed.path.endswith("/v1") else parsed.path
    api_root = urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))
    alias = f"agent-eval-{run_id}"
    response = _post_with_retry(
        f"{api_root}/key/generate",
        headers={"Authorization": f"Bearer {master_key}"},
        payload={
            "key_alias": alias,
            "duration": "1h",
            "models": [model],
            "metadata": {"agent_eval_run_id": run_id},
        },
    )
    key = str(response.json().get("key") or "")
    if not key:
        raise RuntimeError("LiteLLM key generation returned no key")
    return TraceKey(key=key, alias=alias, api_root=api_root, master_key=master_key)


def delete_trace_key(trace_key: TraceKey | None) -> None:
    if trace_key is None:
        return
    _post_with_retry(
        f"{trace_key.api_root}/key/delete",
        headers={"Authorization": f"Bearer {trace_key.master_key}"},
        payload={"keys": [trace_key.key]},
    )
