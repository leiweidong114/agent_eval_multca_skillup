from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from maeval.adapters import _run_process, get_adapter
from maeval.models import Candidate, ScorerSpec, Task

from .engine import KIND_TO_ADAPTER
from .security import SecretBox


def test_provider_connection(
    row: dict[str, Any], secrets: SecretBox, root: Path
) -> dict[str, Any]:
    """Run the same live connectivity probe for manual and scheduled checks."""
    settings = json.loads(row.get("settings_json") or "{}")
    adapter_name = KIND_TO_ADAPTER[row["kind"]]
    if adapter_name.endswith("_agent"):
        try:
            if row["kind"] == "custom_http_agent":
                if not row.get("base_url"):
                    raise ValueError("base URL is not configured")
                headers: dict[str, str] = {}
                token = secrets.decrypt(row.get("api_key_cipher"))
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                request = urllib.request.Request(
                    row["base_url"].rstrip("/") + "/health", headers=headers
                )
                with urllib.request.urlopen(
                    request,
                    timeout=min(30, int(settings.get("timeout_seconds", 30))),
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return {
                    "ok": bool(payload.get("ok", True)),
                    "validation": "agent_health",
                    "message": json.dumps(payload, ensure_ascii=False)[:500],
                    "actual_model": payload.get("model"),
                    "duration_ms": None,
                }
            binary_by_kind = {
                "claude_code_agent": "claude",
                "openclaw_agent": "openclaw",
                "codex_agent": "codex",
            }
            if row["kind"] == "custom_cli_agent":
                command = settings.get("health_command") or settings.get("command")
                if not command:
                    raise ValueError("health_command or command is required")
                command = list(command) + (
                    [] if settings.get("health_command") else ["--health"]
                )
            else:
                command = [binary_by_kind[row["kind"]], "--version"]
            code, stdout, stderr, duration = _run_process(
                command, cwd=root, timeout_seconds=30, env=None
            )
            return {
                "ok": code == 0,
                "validation": "agent_health",
                "message": (stdout or stderr).strip()[:500],
                "actual_model": row.get("model"),
                "duration_ms": duration,
            }
        except Exception as exc:
            return {
                "ok": False,
                "validation": "agent_health",
                "message": str(exc),
                "actual_model": None,
                "duration_ms": None,
            }

    candidate = Candidate(
        id=str(row["id"]),
        adapter=adapter_name,
        model=row["model"],
        base_url=row.get("base_url"),
        api_key=secrets.decrypt(row.get("api_key_cipher")),
        timeout_seconds=int(settings.get("timeout_seconds", 60)),
        max_tokens=32,
        switch_model=bool(settings.get("switch_model", False)),
    )
    task = Task(
        id="connection",
        kind="direct",
        prompt="Reply with exactly OK.",
        scorer=ScorerSpec(type="contains", expected="OK"),
        max_tokens=32,
    )
    result = get_adapter(adapter_name).run(candidate, task, None)
    return {
        "ok": result.ok,
        "validation": "live",
        "message": result.text[:200] if result.ok else result.error,
        "actual_model": result.actual_model,
        "duration_ms": result.duration_api_ms,
    }
