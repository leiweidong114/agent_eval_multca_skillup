from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx


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
    response = httpx.post(
        f"{api_root}/key/generate",
        headers={"Authorization": f"Bearer {master_key}"},
        json={
            "key_alias": alias,
            "duration": "1h",
            "models": [model],
            "metadata": {"agent_eval_run_id": run_id},
            "tags": ["agent-eval", f"run:{run_id}"],
        },
        timeout=15,
    )
    response.raise_for_status()
    key = str(response.json().get("key") or "")
    if not key:
        raise RuntimeError("LiteLLM key generation returned no key")
    return TraceKey(key=key, alias=alias, api_root=api_root, master_key=master_key)


def delete_trace_key(trace_key: TraceKey | None) -> None:
    if trace_key is None:
        return
    response = httpx.post(
        f"{trace_key.api_root}/key/delete",
        headers={"Authorization": f"Bearer {trace_key.master_key}"},
        json={"keys": [trace_key.key]},
        timeout=15,
    )
    response.raise_for_status()
