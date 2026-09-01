from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import AdapterResult, Candidate, Task


class Adapter(ABC):
    @abstractmethod
    def run(
        self, candidate: Candidate, task: Task, workdir: Path | None
    ) -> AdapterResult:
        raise NotImplementedError


def _run_process(
    command: list[str],
    *,
    cwd: Path | None,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, int]:
    command = command.copy()
    resolved = resolve_executable(command[0])
    if resolved:
        command[0] = resolved
    start = time.perf_counter()
    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        duration_ms = int((time.perf_counter() - start) * 1000)
        raise TimeoutError(
            f"command timed out after {timeout_seconds}s; "
            f"stdout={stdout[-500:]!r}; stderr={stderr[-500:]!r}"
        )
    duration_ms = int((time.perf_counter() - start) * 1000)
    return proc.returncode, stdout, stderr, duration_ms


def resolve_executable(name: str) -> str | None:
    if os.name != "nt":
        return shutil.which(name)
    path = Path(name)
    if path.suffix or path.parent != Path("."):
        return str(path) if path.exists() else shutil.which(name)
    for suffix in (".exe", ".com", ".cmd", ".bat"):
        resolved = shutil.which(name + suffix)
        if resolved:
            return resolved
    return shutil.which(name)


def _parse_json_with_prefix(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"no JSON object found in output: {text[-1000:]}")


def _usage_value(usage: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = usage.get(name)
        if value is not None:
            return int(value)
    return None


class DirectHttpAdapter(Adapter):
    def run(
        self, candidate: Candidate, task: Task, workdir: Path | None
    ) -> AdapterResult:
        if task.kind != "direct":
            return AdapterResult(
                ok=False, error="direct_http only supports direct tasks"
            )
        if not candidate.base_url or not (candidate.api_key_env or candidate.api_key):
            return AdapterResult(
                ok=False,
                error="direct_http requires base_url and api_key_env",
            )
        api_key = candidate.api_key or os.environ.get(candidate.api_key_env)
        if not api_key:
            return AdapterResult(
                ok=False,
                error=f"environment variable {candidate.api_key_env} is not set",
            )
        url = candidate.base_url.rstrip("/") + "/v1/messages"
        payload: dict[str, Any] = {
            "model": candidate.model,
            "max_tokens": task.max_tokens or candidate.max_tokens,
            "messages": [{"role": "user", "content": task.prompt}],
        }
        if task.system:
            payload["system"] = task.system
        if candidate.temperature is not None:
            payload["temperature"] = candidate.temperature
        if candidate.thinking_budget_tokens is not None:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": candidate.thinking_budget_tokens,
            }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Api-Key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(
                request, timeout=candidate.timeout_seconds
            ) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return AdapterResult(ok=False, error=f"HTTP request failed: {exc}")
        duration_ms = int((time.perf_counter() - start) * 1000)
        text = "".join(
            block.get("text", "")
            for block in raw.get("content", [])
            if block.get("type") == "text"
        )
        usage = raw.get("usage", {})
        return AdapterResult(
            ok=True,
            text=text,
            raw=raw,
            duration_api_ms=duration_ms,
            input_tokens=_usage_value(usage, "input_tokens", "input"),
            output_tokens=_usage_value(usage, "output_tokens", "output"),
            cache_read_tokens=_usage_value(
                usage, "cache_read_input_tokens", "cacheRead"
            ),
            actual_model=raw.get("model"),
        )


class ClaudeCliAdapter(Adapter):
    def __init__(self, agent_mode: bool) -> None:
        self.agent_mode = agent_mode

    def run(
        self, candidate: Candidate, task: Task, workdir: Path | None
    ) -> AdapterResult:
        if self.agent_mode and task.kind != "repo":
            return AdapterResult(
                ok=False, error="claude_cli_agent only supports repo tasks"
            )
        if not self.agent_mode and task.kind != "direct":
            return AdapterResult(
                ok=False, error="claude_cli_direct only supports direct tasks"
            )
        prompt = task.prompt
        command = [
            "claude",
            "-p",
            prompt,
            "--model",
            candidate.model,
            "--output-format",
            "json",
            "--no-session-persistence",
        ]
        if task.system:
            command.extend(["--system-prompt", task.system])
        if self.agent_mode:
            if workdir is None:
                return AdapterResult(ok=False, error="repo task has no workdir")
            prompt = _agent_prompt(task, workdir)
            command[2] = prompt
            command.extend(
                [
                    "--dangerously-skip-permissions",
                    "--add-dir",
                    str(workdir),
                ]
            )
        else:
            command.extend(["--tools", ""])
        command.extend(candidate.extra_args)
        try:
            code, stdout, stderr, _ = _run_process(
                command,
                cwd=workdir,
                timeout_seconds=candidate.timeout_seconds,
                env=os.environ.copy(),
            )
            raw = _parse_json_with_prefix(stdout)
        except (OSError, TimeoutError, ValueError) as exc:
            return AdapterResult(ok=False, error=str(exc))
        is_error = bool(raw.get("is_error")) or code != 0
        usage = raw.get("usage", {})
        model_usage = raw.get("modelUsage", {})
        actual_model = next(iter(model_usage), None)
        return AdapterResult(
            ok=not is_error,
            text=str(raw.get("result", "")),
            raw=raw,
            error=(str(raw.get("result")) if is_error else None)
            or (stderr[-1000:] if code else None),
            duration_api_ms=raw.get("duration_api_ms"),
            input_tokens=_usage_value(usage, "input_tokens"),
            output_tokens=_usage_value(usage, "output_tokens"),
            cache_read_tokens=_usage_value(usage, "cache_read_input_tokens"),
            cost_usd=raw.get("total_cost_usd"),
            actual_model=actual_model,
        )


class OpenAiHttpAdapter(Adapter):
    def run(
        self, candidate: Candidate, task: Task, workdir: Path | None
    ) -> AdapterResult:
        if task.kind != "direct":
            return AdapterResult(
                ok=False, error="openai_http only supports direct tasks"
            )
        if not candidate.base_url:
            return AdapterResult(ok=False, error="openai_http requires base_url")
        api_key = candidate.api_key or (
            os.environ.get(candidate.api_key_env)
            if candidate.api_key_env
            else None
        )
        if not api_key:
            return AdapterResult(ok=False, error="API key is not configured")
        url = candidate.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        messages: list[dict[str, str]] = []
        if task.system:
            messages.append({"role": "system", "content": task.system})
        messages.append({"role": "user", "content": task.prompt})
        payload: dict[str, Any] = {
            "model": candidate.model,
            "max_tokens": task.max_tokens or candidate.max_tokens,
            "messages": messages,
        }
        if candidate.temperature is not None:
            payload["temperature"] = candidate.temperature
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(
                request, timeout=candidate.timeout_seconds
            ) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            return AdapterResult(ok=False, error=f"HTTP request failed: {exc}")
        duration_ms = int((time.perf_counter() - start) * 1000)
        choice = (raw.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = raw.get("usage") or {}
        return AdapterResult(
            ok=True,
            text=str(message.get("content") or ""),
            raw=raw,
            duration_api_ms=duration_ms,
            input_tokens=_usage_value(usage, "prompt_tokens", "input_tokens"),
            output_tokens=_usage_value(
                usage, "completion_tokens", "output_tokens"
            ),
            cache_read_tokens=_usage_value(usage, "cached_tokens"),
            actual_model=raw.get("model") or candidate.model,
        )


class CodexCliAdapter(Adapter):
    def __init__(self, agent_mode: bool) -> None:
        self.agent_mode = agent_mode

    def run(self, candidate: Candidate, task: Task, workdir: Path | None) -> AdapterResult:
        if self.agent_mode and task.kind != "repo":
            return AdapterResult(ok=False, error="codex_cli_agent only supports repo tasks")
        if not self.agent_mode and task.kind != "direct":
            return AdapterResult(ok=False, error="codex_cli_direct only supports direct tasks")
        if self.agent_mode and workdir is None:
            return AdapterResult(ok=False, error="repo task has no workdir")
        with tempfile.TemporaryDirectory(prefix="maeval-codex-") as temporary:
            cwd = workdir or Path(temporary)
            output_file = Path(temporary) / "last-message.txt"
            prompt = _agent_prompt(task, cwd) if self.agent_mode else task.prompt
            command = [
                "codex",
                "exec",
                "--model",
                candidate.model,
                "--sandbox",
                "workspace-write" if self.agent_mode else "read-only",
                "--cd",
                str(cwd),
                "--skip-git-repo-check",
                "--ephemeral",
                "--json",
                "--output-last-message",
                str(output_file),
                *candidate.extra_args,
                prompt,
            ]
            try:
                code, stdout, stderr, duration_ms = _run_process(
                    command,
                    cwd=cwd,
                    timeout_seconds=candidate.timeout_seconds,
                    env=os.environ.copy(),
                )
            except (OSError, TimeoutError) as exc:
                return AdapterResult(ok=False, error=str(exc))
            text = output_file.read_text(encoding="utf-8", errors="replace") if output_file.is_file() else ""
            input_tokens = output_tokens = None
            events: list[dict[str, Any]] = []
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(event)
                usage = event.get("usage") or event.get("token_usage") or {}
                if usage:
                    input_tokens = _usage_value(usage, "input_tokens", "input") or input_tokens
                    output_tokens = _usage_value(usage, "output_tokens", "output") or output_tokens
            return AdapterResult(
                ok=code == 0 and bool(text.strip()),
                text=text,
                raw={"events": events},
                error=(stderr[-1000:] if code else None) or ("Codex returned no final message" if not text.strip() else None),
                duration_api_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                actual_model=candidate.model,
            )


class CustomCliAgentAdapter(Adapter):
    def run(self, candidate: Candidate, task: Task, workdir: Path | None) -> AdapterResult:
        if task.kind != "repo" or workdir is None:
            return AdapterResult(ok=False, error="custom_cli_agent requires a repository task")
        if not candidate.command:
            return AdapterResult(ok=False, error="custom CLI agent command is not configured")
        with tempfile.TemporaryDirectory(prefix="maeval-agent-contract-") as temporary:
            request_path = Path(temporary) / "request.json"
            response_path = Path(temporary) / "response.json"
            request_payload = {
                "schema_version": "1",
                "run_id": str(uuid.uuid4()),
                "task": {"id": task.id, "type": "repository_agent", "prompt": task.prompt},
                "workspace": {"path": str(workdir), "access": "read_write"},
                "budget": {
                    "timeout_seconds": candidate.timeout_seconds,
                    "max_output_tokens": candidate.max_tokens,
                },
                "model": {"id": candidate.model},
            }
            request_path.write_text(json.dumps(request_payload, ensure_ascii=False), encoding="utf-8")
            replacements = {
                "{request}": str(request_path),
                "{response}": str(response_path),
                "{workspace}": str(workdir),
                "{model}": candidate.model,
            }
            command = []
            used_template = False
            for part in candidate.command:
                rendered = part
                for marker, value in replacements.items():
                    if marker in rendered:
                        used_template = True
                        rendered = rendered.replace(marker, value)
                command.append(rendered)
            if not used_template:
                command.extend(["--request", str(request_path), "--response", str(response_path)])
            env = os.environ.copy()
            if candidate.api_key:
                env["PRISM_AGENT_TOKEN"] = candidate.api_key
            try:
                code, stdout, stderr, duration_ms = _run_process(
                    command, cwd=workdir, timeout_seconds=candidate.timeout_seconds, env=env
                )
            except (OSError, TimeoutError) as exc:
                return AdapterResult(ok=False, error=str(exc))
            try:
                raw = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return AdapterResult(ok=False, text=stdout, error=f"invalid agent response: {exc}")
            usage = raw.get("usage") or {}
            status = raw.get("status")
            return AdapterResult(
                ok=code == 0 and status == "completed",
                text=str(raw.get("summary") or stdout),
                raw=raw,
                error=raw.get("error") or (stderr[-1000:] if code else None),
                duration_api_ms=int((raw.get("execution") or {}).get("duration_ms") or duration_ms),
                input_tokens=_usage_value(usage, "input_tokens", "input"),
                output_tokens=_usage_value(usage, "output_tokens", "output"),
                cost_usd=usage.get("cost_usd"),
                actual_model=raw.get("actual_model") or candidate.model,
            )


class CustomHttpAgentAdapter(Adapter):
    def run(self, candidate: Candidate, task: Task, workdir: Path | None) -> AdapterResult:
        if task.kind != "repo" or workdir is None:
            return AdapterResult(ok=False, error="custom_http_agent requires a repository task")
        if not candidate.base_url:
            return AdapterResult(ok=False, error="custom HTTP agent base URL is not configured")
        payload = {
            "schema_version": "1",
            "run_id": str(uuid.uuid4()),
            "task": {"id": task.id, "type": "repository_agent", "prompt": task.prompt},
            "workspace": {"path": str(workdir), "access": "read_write"},
            "budget": {"timeout_seconds": candidate.timeout_seconds, "max_output_tokens": candidate.max_tokens},
            "model": {"id": candidate.model},
        }
        headers = {"Content-Type": "application/json"}
        if candidate.api_key:
            headers["Authorization"] = f"Bearer {candidate.api_key}"
        request = urllib.request.Request(
            candidate.base_url.rstrip("/") + "/runs",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=candidate.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            return AdapterResult(ok=False, error=f"custom agent HTTP request failed: {exc}")
        duration_ms = int((time.perf_counter() - start) * 1000)
        usage = raw.get("usage") or {}
        return AdapterResult(
            ok=raw.get("status") == "completed",
            text=str(raw.get("summary") or ""),
            raw=raw,
            error=raw.get("error"),
            duration_api_ms=int((raw.get("execution") or {}).get("duration_ms") or duration_ms),
            input_tokens=_usage_value(usage, "input_tokens", "input"),
            output_tokens=_usage_value(usage, "output_tokens", "output"),
            cost_usd=usage.get("cost_usd"),
            actual_model=raw.get("actual_model") or candidate.model,
        )


def _agent_prompt(task: Task, workdir: Path) -> str:
    return (
        "You are being evaluated in an isolated task directory.\n"
        f"Task directory: {workdir}\n"
        "Only modify files inside that directory. Inspect the repository, "
        "implement the requested change, and run relevant tests. Do not merely "
        "describe a patch: make the changes. Finish with a concise summary.\n\n"
        f"Task:\n{task.prompt}"
    )


class OpenClawAdapter(Adapter):
    def __init__(self, agent_mode: bool = True) -> None:
        self.agent_mode = agent_mode

    def run(
        self, candidate: Candidate, task: Task, workdir: Path | None
    ) -> AdapterResult:
        if self.agent_mode and task.kind != "repo":
            return AdapterResult(
                ok=False, error="openclaw_agent only supports repo tasks"
            )
        if not self.agent_mode and task.kind != "direct":
            return AdapterResult(
                ok=False, error="openclaw_direct only supports direct tasks"
            )
        if self.agent_mode and workdir is None:
            return AdapterResult(ok=False, error="repo task has no workdir")
        cwd = workdir or Path.cwd()
        session_id = str(uuid.uuid4())
        env = os.environ.copy()
        no_proxy = [
            value.strip()
            for value in env.get("NO_PROXY", "").split(",")
            if value.strip()
        ]
        for host in ("127.0.0.1", "localhost", "api.minimaxi.com"):
            if host not in no_proxy:
                no_proxy.append(host)
        env["NO_PROXY"] = ",".join(no_proxy)

        if candidate.switch_model:
            switch = self._invoke(
                candidate,
                cwd,
                session_id,
                f"/model {candidate.model}",
                env,
            )
            if not switch.ok:
                return switch

        return self._invoke(
            candidate,
            cwd,
            session_id,
            _agent_prompt(task, cwd) if self.agent_mode else task.prompt,
            env,
        )

    def _invoke(
        self,
        candidate: Candidate,
        workdir: Path,
        session_id: str,
        message: str,
        env: dict[str, str],
    ) -> AdapterResult:
        command = [
            *_openclaw_command_prefix(),
            "agent",
            "--local",
            "--session-id",
            session_id,
            "--message",
            message,
            "--thinking",
            "off",
            "--json",
            "--timeout",
            str(candidate.timeout_seconds),
        ]
        command.extend(candidate.extra_args)
        try:
            code, stdout, stderr, _ = _run_process(
                command,
                cwd=workdir,
                timeout_seconds=candidate.timeout_seconds + 120,
                env=env,
            )
            try:
                raw = _parse_json_with_prefix(stdout)
            except ValueError:
                raw = _parse_json_with_prefix(stderr)
        except (OSError, TimeoutError, ValueError) as exc:
            return AdapterResult(ok=False, error=str(exc))
        payloads = raw.get("payloads", [])
        text = "\n".join(
            str(item.get("text", "")) for item in payloads if item.get("text")
        )
        meta = raw.get("meta", {})
        agent_meta = meta.get("agentMeta", {})
        usage = agent_meta.get("lastCallUsage") or agent_meta.get("usage") or {}
        aborted = bool(meta.get("aborted"))
        ok = code == 0 and not aborted and bool(text.strip())
        return AdapterResult(
            ok=ok,
            text=text,
            raw=raw,
            error=(stderr[-1000:] if code else None)
            or ("OpenClaw run was aborted" if aborted else None)
            or ("OpenClaw returned no response payload" if not text.strip() else None),
            duration_api_ms=meta.get("durationMs"),
            input_tokens=_usage_value(usage, "input"),
            output_tokens=_usage_value(usage, "output"),
            cache_read_tokens=_usage_value(usage, "cacheRead"),
            actual_model=agent_meta.get("model"),
        )


def _openclaw_command_prefix() -> list[str]:
    resolved = resolve_executable("openclaw")
    if not resolved:
        return ["openclaw"]
    path = Path(resolved)
    if os.name == "nt" and path.suffix.lower() in {".cmd", ".bat"}:
        script = path.parent / "node_modules" / "openclaw" / "openclaw.mjs"
        node = resolve_executable("node")
        if script.is_file() and node:
            return [node, str(script)]
    return [resolved]


def get_adapter(name: str) -> Adapter:
    adapters: dict[str, Adapter] = {
        "direct_http": DirectHttpAdapter(),
        "openai_http": OpenAiHttpAdapter(),
        "claude_cli_direct": ClaudeCliAdapter(agent_mode=False),
        "claude_cli_agent": ClaudeCliAdapter(agent_mode=True),
        "openclaw_agent": OpenClawAdapter(agent_mode=True),
        "openclaw_direct": OpenClawAdapter(agent_mode=False),
        "codex_cli_direct": CodexCliAdapter(agent_mode=False),
        "codex_cli_agent": CodexCliAdapter(agent_mode=True),
        "custom_cli_agent": CustomCliAgentAdapter(),
        "custom_http_agent": CustomHttpAgentAdapter(),
    }
    return adapters[name]
