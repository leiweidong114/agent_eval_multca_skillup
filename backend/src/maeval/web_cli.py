from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any


PROVIDER_KINDS = (
    "anthropic",
    "openai",
    "claude_code_direct",
    "claude_code_agent",
    "openclaw_direct",
    "openclaw_agent",
    "codex_direct",
    "codex_agent",
    "custom_http_agent",
    "custom_cli_agent",
)
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


class PlatformApiError(RuntimeError):
    pass


class PlatformClient:
    def __init__(self, server: str, username: str, password: str) -> None:
        self.server = server.rstrip("/")
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )
        self.request(
            "POST",
            "/api/auth/login",
            {"username": username, "password": password},
        )

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout_seconds: float = 30,
    ) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.server + path, data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail")
            except Exception:
                detail = None
            raise PlatformApiError(detail or f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise PlatformApiError(
                f"cannot connect to {self.server}: {exc.reason}"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise PlatformApiError(
                f"request timed out after {timeout_seconds:g} seconds: {path}"
            ) from exc


def add_platform_parser(subparsers: Any) -> None:
    platform = subparsers.add_parser(
        "platform", help="Manage and run the Web evaluation platform."
    )
    platform.add_argument(
        "--server",
        default=os.environ.get("PRISM_SERVER", "http://127.0.0.1:8765"),
    )
    platform.add_argument(
        "--username", default=os.environ.get("PRISM_USERNAME", "admin")
    )
    platform.add_argument(
        "--password-env",
        default="PRISM_PASSWORD",
        help="Environment variable containing the platform password.",
    )
    platform.add_argument("--json", action="store_true", dest="json_output")
    resources = platform.add_subparsers(dest="platform_resource", required=True)

    provider = resources.add_parser("provider", help="Manage models and agents.")
    provider_actions = provider.add_subparsers(dest="provider_action", required=True)
    provider_actions.add_parser("list")
    add = provider_actions.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--kind", required=True, choices=PROVIDER_KINDS)
    add.add_argument("--model", required=True)
    add.add_argument("--base-url")
    add.add_argument("--api-key")
    add.add_argument(
        "--api-key-env",
        help="Read the model/agent API key from this environment variable.",
    )
    add.add_argument("--settings-json", default="{}")
    add.add_argument("--timeout", type=int)
    add.add_argument("--max-tokens", type=int)
    add.add_argument("--shared", action="store_true")
    test = provider_actions.add_parser("test")
    test.add_argument("provider", help="Provider numeric ID or exact name.")

    benchmark = resources.add_parser("benchmark", help="Inspect benchmark packs.")
    benchmark.add_subparsers(dest="benchmark_action", required=True).add_parser(
        "list"
    )

    evaluation = resources.add_parser("evaluate", help="Start and inspect evaluations.")
    evaluation_actions = evaluation.add_subparsers(
        dest="evaluation_action", required=True
    )
    start = evaluation_actions.add_parser("start")
    start.add_argument("--name", required=True)
    start.add_argument("--provider", action="append", required=True)
    start.add_argument("--benchmark", action="append", required=True)
    start.add_argument(
        "--track",
        choices=("model_direct", "reference_agent", "native_agent"),
        default="model_direct",
    )
    start.add_argument("--sample-limit", type=int)
    start.add_argument("--repeats", type=int, default=1)
    start.add_argument("--concurrency", type=int, default=1)
    start.add_argument("--random-seed", type=int, default=42)
    start.add_argument(
        "--sampling-strategy",
        choices=("stratified", "random", "ordered"),
        default="stratified",
    )
    start.add_argument("--timeout", type=int, default=300)
    start.add_argument("--max-tokens", type=int, default=4096)
    start.add_argument("--max-context-chars", type=int, default=60000)
    start.add_argument("--max-files-changed", type=int, default=8)
    start.add_argument("--allow-unsafe-code", action="store_true")
    start.add_argument("--wait", action="store_true")
    start.add_argument("--wait-timeout", type=int, default=86400)
    start.add_argument("--poll-interval", type=float, default=2.0)
    evaluation_actions.add_parser("list")
    show = evaluation_actions.add_parser("show")
    show.add_argument("experiment_id", type=int)


def _password(args: argparse.Namespace) -> str:
    password = os.environ.get(args.password_env)
    if password:
        return password
    if not sys.stdin.isatty():
        raise PlatformApiError(
            f"set {args.password_env} or run the command in an interactive terminal"
        )
    return getpass.getpass(f"Prism password for {args.username}: ")


def _resolve_provider(client: PlatformClient, reference: str) -> dict[str, Any]:
    providers = client.request("GET", "/api/providers")
    if reference.isdigit():
        matches = [item for item in providers if item["id"] == int(reference)]
    else:
        matches = [item for item in providers if item["name"] == reference]
    if not matches:
        raise PlatformApiError(f"provider not found: {reference}")
    if len(matches) > 1:
        raise PlatformApiError(f"provider name is ambiguous; use numeric ID: {reference}")
    return matches[0]


def _emit(value: Any, json_output: bool) -> None:
    if json_output:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, list):
        for item in value:
            print(_one_line(item))
    elif isinstance(value, dict):
        print(_one_line(value))
    else:
        print(value)


def _one_line(item: dict[str, Any]) -> str:
    if "kind" in item and "model" in item:
        return (
            f"id={item['id']} name={item['name']} kind={item['kind']} "
            f"model={item['model']}"
        )
    if "task_type" in item:
        return (
            f"id={item['id']} name={item['name']} task_type={item['task_type']} "
            f"status={item['status']} items={item['item_count']}"
        )
    if "status" in item and "provider_ids_json" in item:
        return (
            f"id={item['id']} name={item['name']} status={item['status']} "
            f"completed={item['completed_jobs']}/{item['total_jobs']}"
        )
    if "status" in item and "selected_items" in item:
        return (
            f"id={item['id']} name={item['name']} status={item['status']} "
            f"completed={item['completed_jobs']}/{item['total_jobs']} "
            f"passed={item['passed_jobs']} selected_items={item['selected_items']}"
        )
    return json.dumps(item, ensure_ascii=False)


def run_platform(args: argparse.Namespace) -> int:
    try:
        client = PlatformClient(args.server, args.username, _password(args))
        if args.platform_resource == "provider":
            if args.provider_action == "list":
                _emit(client.request("GET", "/api/providers"), args.json_output)
                return 0
            if args.provider_action == "add":
                try:
                    settings = json.loads(args.settings_json)
                except json.JSONDecodeError as exc:
                    raise PlatformApiError(f"invalid --settings-json: {exc}") from exc
                if not isinstance(settings, dict):
                    raise PlatformApiError("--settings-json must contain a JSON object")
                if args.timeout is not None:
                    settings["timeout_seconds"] = args.timeout
                if args.max_tokens is not None:
                    settings["max_tokens"] = args.max_tokens
                api_key = args.api_key
                if args.api_key_env:
                    api_key = os.environ.get(args.api_key_env)
                    if not api_key:
                        raise PlatformApiError(
                            f"environment variable {args.api_key_env} is not set"
                        )
                created = client.request(
                    "POST",
                    "/api/providers",
                    {
                        "name": args.name,
                        "kind": args.kind,
                        "model": args.model,
                        "base_url": args.base_url,
                        "api_key": api_key,
                        "settings": settings,
                        "shared": args.shared,
                    },
                )
                _emit(created, args.json_output)
                return 0
            provider = _resolve_provider(client, args.provider)
            result = client.request(
                "POST", f"/api/providers/{provider['id']}/test", timeout_seconds=600
            )
            _emit({"provider": provider["name"]} | result, args.json_output)
            return 0 if result.get("ok") is not False else 1

        if args.platform_resource == "benchmark":
            _emit(client.request("GET", "/api/benchmarks"), args.json_output)
            return 0

        if args.evaluation_action == "list":
            _emit(client.request("GET", "/api/experiments"), args.json_output)
            return 0
        if args.evaluation_action == "show":
            result = client.request(
                "GET", f"/api/experiments/{args.experiment_id}"
            )
            _emit(result, args.json_output)
            return 0

        providers = [
            _resolve_provider(client, reference) for reference in args.provider
        ]
        available_benchmarks = {
            item["id"]: item for item in client.request("GET", "/api/benchmarks")
        }
        missing = [item for item in args.benchmark if item not in available_benchmarks]
        if missing:
            raise PlatformApiError(f"benchmark not found: {', '.join(missing)}")
        result = client.request(
            "POST",
            "/api/experiments",
            {
                "name": args.name,
                "provider_ids": [item["id"] for item in providers],
                "benchmark_ids": args.benchmark,
                "track": args.track,
                "sample_limit": args.sample_limit,
                "repeats": args.repeats,
                "concurrency": args.concurrency,
                "random_seed": args.random_seed,
                "sampling_strategy": args.sampling_strategy,
                "allow_unsafe_code": args.allow_unsafe_code,
                "budget": {
                    "timeout_seconds_per_task": args.timeout,
                    "max_output_tokens": args.max_tokens,
                    "max_context_chars": args.max_context_chars,
                    "max_files_changed": args.max_files_changed,
                },
            },
        )
        if args.wait:
            deadline = time.monotonic() + args.wait_timeout
            while result["status"] not in TERMINAL_STATUSES:
                if time.monotonic() >= deadline:
                    raise PlatformApiError(
                        f"wait timed out; experiment {result['id']} is {result['status']}"
                    )
                time.sleep(max(0.2, args.poll_interval))
                result = client.request("GET", f"/api/experiments/{result['id']}")
        _emit(result, args.json_output)
        return 0 if result["status"] not in {"failed", "interrupted"} else 1
    except PlatformApiError as exc:
        print(f"Platform error: {exc}", file=sys.stderr)
        return 2
