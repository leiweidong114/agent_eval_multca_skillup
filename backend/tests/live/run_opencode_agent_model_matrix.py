"""Live OpenCode-model x core-six-Agent compatibility matrix.

This is intentionally not named ``test_*.py``: it creates real LiteLLM virtual
keys, launches installed Agent CLIs, consumes model quota, and queries the
LiteLLM PostgreSQL database. Run it explicitly from the repository root.

Every Agent receives exactly one user message: ``HI``. A row passes only when
the Agent exits successfully, the run-scoped virtual key matches at least one
successful SpendLogs row, the requested model is verified, and the temporary
key is deleted.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any

import httpx


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent
SOURCE_ROOT = BACKEND_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from agent_eval.cli import _check_agent  # noqa: E402
from agent_eval.model_config import discover_available_models, resolve_model_profile  # noqa: E402
from agent_eval.runtime import backend_agent, default_agent_command  # noqa: E402


CORE_AGENTS = ("claude", "codebuddy", "codex", "justdo", "openclaw", "opencode")
SECRET_PATTERN = re.compile(r"(?i)(?:sk|key)-[A-Za-z0-9_.-]{12,}")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send HI through every core Agent and selected LiteLLM models"
    )
    parser.add_argument("--profile", default="litellm_opencode_go")
    parser.add_argument("--prefix", default="opencode-go/")
    parser.add_argument("--agent", action="append", dest="agents")
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--preflight-timeout", type=int, default=45)
    parser.add_argument(
        "--preflight", action=argparse.BooleanOptionalAction, default=True,
        help="Send HI directly to each catalog model and run the Agent matrix only for HTTP-successful models",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--resume", action="store_true",
        help="Keep completed rows in matrix-results.json and run only missing combinations",
    )
    return parser.parse_args()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if any(token in str(key).lower() for token in ("password", "secret", "api_key", "authorization"))
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_PATTERN.sub("[REDACTED]", value)
    return value


def _git_commit() -> str | None:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT,
        text=True, capture_output=True, check=False,
    )
    return process.stdout.strip() if process.returncode == 0 else None


def _discover(prefix: str) -> tuple[list[str], dict[str, Any]]:
    catalog = discover_available_models(BACKEND_ROOT)
    models = sorted(
        item["id"]
        for item in catalog.get("models", [])
        if item.get("source") == "litellm" and str(item.get("id", "")).startswith(prefix)
    )
    return models, catalog


def _row_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return str(row["agent"]), str(row["requested_model"]), int(row["attempt"])


def _run_one(
    *, agent: str, model: str, attempt: int, profile: str, timeout: int
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    args = argparse.Namespace(
        agent=agent,
        profile=profile,
        model=model,
        agent_executable=None,
        timeout=timeout,
        database_verify=True,
        prompt="HI",
    )
    try:
        raw = _check_agent(args)
    except Exception as exc:  # Preserve the remaining matrix when one adapter crashes.
        raw = {
            "status": "exception",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    finished = datetime.now(timezone.utc)
    trace = raw.get("database_trace") or {}
    verification = raw.get("model_verification") or {}
    cleanup = raw.get("trace_key_cleanup") or {}
    passed = bool(
        raw.get("status") == "connected"
        and bool(str(raw.get("response") or "").strip())
        and verification.get("verified") is True
        and int(trace.get("successful_model_calls") or 0) >= 1
        and cleanup.get("status") == "deleted"
    )
    return _redact(
        {
            "agent": agent,
            "requested_model": model,
            "attempt": attempt,
            "prompt": "HI",
            "passed": passed,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 3),
            "status": raw.get("status"),
            "agent_model": raw.get("agent_model"),
            "runtime_exit_code": raw.get("runtime_exit_code"),
            "agent_exit_code": raw.get("agent_exit_code"),
            "response": str(raw.get("response") or "")[:2000],
            "error_type": raw.get("error_type"),
            "error": str(raw.get("error") or "")[:4000] or None,
            "database_trace": trace,
            "model_verification": verification,
            "trace_key_alias": raw.get("trace_key_alias"),
            "trace_key_cleanup": cleanup,
        }
    )


def _preflight_one(model: str, profile_name: str, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    status_code = None
    try:
        profile = resolve_model_profile(
            BACKEND_ROOT, profile_name=profile_name, model_override=model, agent="opencode"
        )
        response = httpx.post(
            profile.api_base.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + profile.environment[profile.api_key_env]},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "HI"}],
                "max_tokens": 64,
                "stream": False,
            },
            timeout=timeout,
        )
        status_code = response.status_code
        body = response.text[:2000]
        passed = response.is_success
        error = None if passed else body
    except Exception as exc:
        passed = False
        error = f"{type(exc).__name__}: {exc}"
        body = ""
    return _redact(
        {
            "model": model,
            "prompt": "HI",
            "passed": passed,
            "http_status": status_code,
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": error,
            "response_excerpt": body[:500] if passed else None,
        }
    )


def _resolve_adapter(agent: str, model: str, profile_name: str) -> dict[str, Any]:
    try:
        runtime_agent = backend_agent(agent)
        profile = resolve_model_profile(
            BACKEND_ROOT,
            profile_name=profile_name,
            model_override=model,
            agent=runtime_agent,
        )
        agent_model = profile.model_for_agent(runtime_agent)
        gateway_model = profile.gateway_model_for_agent(runtime_agent)
        opencode_config = profile.environment.get("OPENCODE_CONFIG_CONTENT", "")
        reasoning_enabled = (
            '"reasoning": true' in opencode_config.lower()
            if runtime_agent == "opencode"
            else True
        )
        gateway_is_expected = gateway_model.lower() in {
            model.lower(),
            f"{model}-anthropic".lower(),
        }
        passed = bool(
            profile.model == model
            and gateway_is_expected
            and "no-thinking" not in gateway_model.lower()
            and reasoning_enabled
        )
        return {
            "agent": agent,
            "runtime_agent": runtime_agent,
            "requested_model": model,
            "agent_model": agent_model,
            "gateway_model": gateway_model,
            "protocol": profile.protocol,
            "reasoning_enabled": reasoning_enabled,
            "passed": passed,
            "error": None,
        }
    except Exception as exc:
        return {
            "agent": agent,
            "requested_model": model,
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = (
        "agent", "requested_model", "attempt", "passed", "status",
        "agent_model", "runtime_exit_code", "agent_exit_code",
        "db_status", "db_calls", "db_successes", "db_models",
        "model_verified", "verification_reason", "trace_key_alias",
        "trace_key_cleanup", "duration_seconds", "error",
    )
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            trace = row.get("database_trace") or {}
            verification = row.get("model_verification") or {}
            cleanup = row.get("trace_key_cleanup") or {}
            writer.writerow(
                {
                    "agent": row["agent"],
                    "requested_model": row["requested_model"],
                    "attempt": row["attempt"],
                    "passed": row["passed"],
                    "status": row.get("status"),
                    "agent_model": row.get("agent_model"),
                    "runtime_exit_code": row.get("runtime_exit_code"),
                    "agent_exit_code": row.get("agent_exit_code"),
                    "db_status": trace.get("status"),
                    "db_calls": trace.get("model_call_count"),
                    "db_successes": trace.get("successful_model_calls"),
                    "db_models": "|".join(trace.get("models") or []),
                    "model_verified": verification.get("verified"),
                    "verification_reason": verification.get("reason"),
                    "trace_key_alias": row.get("trace_key_alias"),
                    "trace_key_cleanup": cleanup.get("status"),
                    "duration_seconds": row.get("duration_seconds"),
                    "error": row.get("error"),
                }
            )


def _write_summary(
    path: Path, *, agents: list[str], models: list[str], rows: list[dict[str, Any]]
) -> None:
    total = len(rows)
    passed = sum(bool(row.get("passed")) for row in rows)
    lines = [
        "# OpenCode 模型 × 六 Agent HI 在线矩阵摘要",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 模型数：{len(models)}",
        f"- Agent 数：{len(agents)}",
        f"- 已完成尝试：{total}",
        f"- 通过：{passed}",
        f"- 失败：{total - passed}",
        "- 单条用户消息：`HI`",
        "- 通过定义：Agent 成功 + trace alias 精确命中成功 SpendLogs + 指定模型匹配 + trace key 删除成功",
        "",
        "| Agent | 通过/总数 | 通过率 |",
        "|---|---:|---:|",
    ]
    for agent in agents:
        selected = [row for row in rows if row["agent"] == agent]
        ok = sum(bool(row.get("passed")) for row in selected)
        rate = 100 * ok / len(selected) if selected else 0
        lines.append(f"| {agent} | {ok}/{len(selected)} | {rate:.2f}% |")
    lines.extend(["", "## 未通过组合", ""])
    failures = [row for row in rows if not row.get("passed")]
    if not failures:
        lines.append("无。")
    else:
        lines.extend([
            "| Agent | 模型 | 尝试 | 状态 | 核验原因 | 错误摘要 |",
            "|---|---|---:|---|---|---|",
        ])
        for row in failures:
            reason = (row.get("model_verification") or {}).get("reason") or ""
            error = str(row.get("error") or "").replace("|", "\\|").replace("\n", " ")[:240]
            lines.append(
                f"| {row['agent']} | `{row['requested_model']}` | {row['attempt']} | "
                f"{row.get('status')} | {reason} | {error} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _arguments()
    if args.repeats < 1 or args.workers < 1 or args.timeout < 1:
        raise SystemExit("repeats, workers, and timeout must be positive")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    agents = list(dict.fromkeys(args.agents or CORE_AGENTS))
    discovered, catalog = _discover(args.prefix)
    catalog_models = list(dict.fromkeys(args.models or discovered))
    if not catalog_models:
        raise SystemExit(f"No LiteLLM models found with prefix {args.prefix!r}")

    adapter_rows = [
        _redact(_resolve_adapter(agent, model, args.profile))
        for model in catalog_models
        for agent in agents
    ]
    _atomic_json(output_dir / "adapter-resolution.json", adapter_rows)

    preflight_rows: list[dict[str, Any]] = []
    if args.preflight:
        preflight_path = output_dir / "model-preflight.json"
        if args.resume and preflight_path.is_file():
            cached = json.loads(preflight_path.read_text(encoding="utf-8"))
            if {str(row.get("model")) for row in cached} == set(catalog_models):
                preflight_rows = cached
                print("[preflight] Reused complete model-preflight.json", flush=True)
        if not preflight_rows:
            with ThreadPoolExecutor(
                max_workers=min(args.workers, len(catalog_models)),
                thread_name_prefix="model-preflight",
            ) as pool:
                futures = {
                    pool.submit(_preflight_one, model, args.profile, args.preflight_timeout): model
                    for model in catalog_models
                }
                for future in as_completed(futures):
                    row = future.result()
                    preflight_rows.append(row)
                    state = "PASS" if row["passed"] else "FAIL"
                    print(
                        f"[preflight] {state} {row['model']} "
                        f"HTTP={row['http_status']} ({row['duration_seconds']}s)",
                        flush=True,
                    )
        preflight_rows.sort(key=lambda row: str(row["model"]))
        _atomic_json(preflight_path, preflight_rows)
        models = [str(row["model"]) for row in preflight_rows if row["passed"]]
    else:
        models = catalog_models

    result_path = output_dir / "matrix-results.json"
    rows: list[dict[str, Any]] = []
    if args.resume and result_path.is_file():
        previous = json.loads(result_path.read_text(encoding="utf-8"))
        rows = list(previous.get("results") or [])
    completed = {_row_key(row) for row in rows}
    tasks = [
        (agent, model, attempt)
        for attempt in range(1, args.repeats + 1)
        for model in models
        for agent in agents
        if (agent, model, attempt) not in completed
    ]
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "profile": args.profile,
        "prompt": "HI",
        "model_prefix": args.prefix,
        "agents": agents,
        "catalog_models": catalog_models,
        "catalog_model_count": len(catalog_models),
        "models": models,
        "operational_model_count": len(models),
        "preflight_enabled": args.preflight,
        "adapter_resolution_passed": sum(bool(row.get("passed")) for row in adapter_rows),
        "adapter_resolution_total": len(adapter_rows),
        "repeats": args.repeats,
        "workers": args.workers,
        "timeout_seconds": args.timeout,
        "catalog_errors": catalog.get("errors") or [],
        "agent_executables": {agent: default_agent_command(agent) for agent in agents},
    }
    _atomic_json(output_dir / "matrix-manifest.json", _redact(manifest))
    if not models:
        _atomic_json(result_path, {"manifest": manifest, "results": []})
        _write_csv(output_dir / "matrix-results.csv", [])
        _write_summary(
            output_dir / "matrix-summary.md", agents=agents, models=models, rows=[]
        )
        print("No catalog model passed the direct HI preflight; Agent matrix skipped.")
        return 1
    lock = threading.Lock()
    total_expected = len(agents) * len(models) * args.repeats
    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="agent-matrix") as pool:
        futures = {
            pool.submit(
                _run_one, agent=agent, model=model, attempt=attempt,
                profile=args.profile, timeout=args.timeout,
            ): (agent, model, attempt)
            for agent, model, attempt in tasks
        }
        for future in as_completed(futures):
            row = future.result()
            with lock:
                rows.append(row)
                rows.sort(key=_row_key)
                payload = {"manifest": manifest, "results": rows}
                _atomic_json(result_path, _redact(payload))
                _write_csv(output_dir / "matrix-results.csv", rows)
                _write_summary(
                    output_dir / "matrix-summary.md", agents=agents, models=models, rows=rows
                )
                done = len(rows)
                state = "PASS" if row["passed"] else "FAIL"
                print(
                    f"[{done}/{total_expected}] {state} {row['agent']} "
                    f"{row['requested_model']} ({row['duration_seconds']}s)",
                    flush=True,
                )
    passed = sum(bool(row.get("passed")) for row in rows)
    print(f"Completed: {passed}/{len(rows)} passed; output={output_dir}")
    return 0 if len(rows) == total_expected and passed == total_expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
