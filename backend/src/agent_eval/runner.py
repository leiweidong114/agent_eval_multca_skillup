from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from threading import Event, Thread
from collections.abc import Callable

import psutil

import yaml

from agent_eval.codebuddy_proxy import CodeBuddyCompatibilityProxy
from agent_eval.database import (
    database_health,
    fetch_model_interactions,
    wait_for_model_interactions,
    summarize_model_interactions,
    verify_requested_model,
)
from agent_eval.model_config import (
    CREDENTIAL_ENV_NAMES,
    resolve_config_secret,
    resolve_model_profile,
    write_openclaw_profile_config,
    write_codebuddy_profile_config,
)
from agent_eval.skill_quality import evaluate_skill_quality
from agent_eval.litellm_trace import TraceKeyError, create_trace_key, delete_trace_key
from agent_eval.failure import describe_evaluation_failure
from agent_eval.agent_contract import assess_agent_contract
from agent_eval.llm_judge import run_llm_judge
from agent_eval.evaluators import resolve_evaluator
from agent_eval.evaluators.protocol import EvaluationContext, EvaluationEvidence, PluginEvaluation
from agent_eval.scoring import (
    collect_process_metrics,
    collect_skill_read_evidence,
    supplement_database_tool_metrics,
    combine_dimensions,
    load_scoring_config,
)
from agent_eval.runtime import (
    agent_capabilities,
    backend_agent,
    default_agent_command,
    find_multica_runtime,
    find_skill_up,
    normalize_agent,
    skill_target,
    validate_evaluation_capabilities,
)


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._") or "run"


def _identity(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}", normalized):
        raise ValueError(f"{field} must be 1-128 safe identifier characters")
    return normalized


def _copy_skill(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            ".git", ".runtime", ".tools", "runs", "__pycache__", "*.pyc"
        ),
    )


class EvaluationCancelled(RuntimeError):
    pass


class EvaluationInfrastructureError(RuntimeError):
    def __init__(self, message: str, *, category: str, retryable: bool) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable


def _retryable_infrastructure_message(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in (
        "timeout", "timed out", "connection", "temporarily unavailable",
        "server closed", "429", "500", "502", "503", "504",
    ))


def classify_evaluation_failure(text: str, returncode: int) -> dict[str, Any] | None:
    return describe_evaluation_failure(text, returncode=returncode)


def cases_completed(results: list[dict[str, Any]]) -> bool:
    cases = [case for result in results for case in result.get("case_results", [])]
    return bool(cases) and all(case.get("status") in {"PASS", "FAIL"} for case in cases)


def _execute_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    cancel_event: Event | None,
    event_callback: Callable[[str, str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def consume(pipe: Any, target: list[str], stream_name: str) -> None:
        try:
            for line in iter(pipe.readline, ""):
                target.append(line)
                if event_callback is not None and line.strip():
                    event_callback(stream_name, line.rstrip("\r\n"))
        finally:
            pipe.close()

    readers = [
        Thread(target=consume, args=(process.stdout, stdout_lines, "stdout"), daemon=True),
        Thread(target=consume, args=(process.stderr, stderr_lines, "stderr"), daemon=True),
    ]
    for reader in readers:
        reader.start()
    while process.poll() is None:
        if cancel_event is None or not cancel_event.is_set():
            time.sleep(0.2)
            continue
        try:
            parent = psutil.Process(process.pid)
            descendants = parent.children(recursive=True)
            for child in descendants:
                child.terminate()
            parent.terminate()
            _, alive = psutil.wait_procs([*descendants, parent], timeout=3)
            for item in alive:
                item.kill()
        except (psutil.Error, OSError):
            process.kill()
        process.wait()
        for reader in readers:
            reader.join(timeout=1)
        raise EvaluationCancelled("Evaluation was cancelled")
    for reader in readers:
        reader.join(timeout=2)
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        "".join(stdout_lines),
        "".join(stderr_lines),
    )


def _generated_case(
    path: Path,
    prompt: str,
    must_contain: list[str],
    must_not_contain: list[str],
) -> None:
    case: dict[str, Any] = {
        "id": "cli-prompt",
        "title": "CLI prompt evaluation",
        "input": {"prompt": prompt},
    }
    if must_contain or must_not_contain:
        case["expect"] = {
            **({"must_contain": must_contain} if must_contain else {}),
            **({"must_not_contain": must_not_contain} if must_not_contain else {}),
        }
    path.write_text(
        yaml.safe_dump(case, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def aggregate_scores(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate transparent 0-100 scores without an LLM judge."""
    buckets: dict[str, dict[str, float]] = {
        "with_skill": {"passed": 0, "total": 0, "completed": 0, "cases": 0},
        "without_skill": {"passed": 0, "total": 0, "completed": 0, "cases": 0},
    }
    total_tokens = 0
    total_duration_ms = 0
    for result in results:
        total_tokens += int(result.get("overall_tokens", result.get("total_tokens", 0)) or 0)
        for case in result.get("case_results", []):
            variant = case.get("configuration", "with_skill")
            if variant not in buckets:
                continue
            bucket = buckets[variant]
            bucket["cases"] += 1
            if str(case.get("status", "")).upper() in {"PASS", "FAIL"}:
                bucket["completed"] += 1
            total_duration_ms += int(case.get("duration_ms", 0) or 0)
            grading = (case.get("grading") or {}).get("summary") or {}
            bucket["passed"] += int(grading.get("passed", 0) or 0)
            bucket["total"] += int(grading.get("total", 0) or 0)

    def percent(numerator: float, denominator: float) -> float | None:
        return round(100 * numerator / denominator, 2) if denominator else None

    task = percent(buckets["with_skill"]["passed"], buckets["with_skill"]["total"])
    baseline = percent(
        buckets["without_skill"]["passed"], buckets["without_skill"]["total"]
    )
    stability = percent(
        buckets["with_skill"]["completed"], buckets["with_skill"]["cases"]
    )
    return {
        "task_score": task,
        "baseline_score": baseline,
        "skill_gain": round(task - baseline, 2)
        if task is not None and baseline is not None
        else None,
        "execution_stability": stability,
        "with_skill_cases": int(buckets["with_skill"]["cases"]),
        "without_skill_cases": int(buckets["without_skill"]["cases"]),
        "total_tokens": total_tokens,
        "total_duration_ms": total_duration_ms,
    }


def attach_session_evidence(iteration_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Add the raw Agent transcript to each summarized Skill-Up case."""
    sessions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for path in iteration_dir.rglob("session-result.json"):
        relative = path.relative_to(iteration_dir).parts
        if len(relative) < 3:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            sessions.setdefault((relative[0], relative[1]), []).append(payload)

    for case in result.get("case_results", []):
        if not isinstance(case, dict):
            continue
        key = (
            str(case.get("case_id") or ""),
            str(case.get("configuration") or "with_skill"),
        )
        evidence = sessions.get(key) or []
        if len(evidence) == 1:
            case["session_result"] = evidence[0]
        elif evidence:
            case["session_results"] = evidence
    return result


def build_eval_config(
    *,
    agent: str,
    model: str | None,
    profile: str | None = None,
    executable: str,
    runtime_binary: Path,
    skill_name: str,
    case_paths: list[Path],
    parallelism: int,
    timeout_seconds: int,
    max_turns: int,
    benchmark: bool,
    extra_args: list[str],
    additional_skills: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    args = [
        "--input",
        "${input_file}",
        "--output",
        "${output_file}",
        "--agent",
        agent,
        "--model",
        "${model_name}",
        "--timeout-seconds",
        str(timeout_seconds),
        "--max-turns",
        str(max_turns),
    ]
    for item in extra_args:
        args.extend(["--extra-arg", item])
    return {
        "schema_version": "v1alpha1",
        "environment": {"type": "none"},
        "skills": [
            {
                "source": "local_path",
                "path": ".",
                "target": skill_target(agent, skill_name),
                "exclude": ["evals/**"],
            }
        ] + [
            {
                "source": "local_path",
                "path": source_path,
                "target": skill_target(agent, child_name),
                "exclude": ["evals/**"],
            }
            for source_path, child_name in (additional_skills or [])
        ],
        "engine": {
            "name": "multica-local",
            "model": {"provider": "local", "name": model},
            "custom": {
                "transport": "local",
                "timeout_seconds": timeout_seconds,
                "response_format": "session_result",
                "env": {
                    # Keep executable paths out of cmd.exe/bash argument
                    # serialization. This is required for Windows paths that
                    # contain spaces or non-ASCII characters.
                    "AGENT_EVAL_AGENT_EXECUTABLE": "${AGENT_EVAL_AGENT_EXECUTABLE}",
                },
                "local": {
                    "command": str(runtime_binary),
                    "args": args,
                    "cwd": "${workspace}",
                    "input_file": "${input_file}",
                    "output_file": "${output_file}",
                },
            },
        },
        "cases": {
            "files": [path.as_posix() for path in case_paths],
            "defaults": {
                "timeout_seconds": timeout_seconds,
                "max_turns": max_turns,
                "collect_artifacts": [
                    "out/**",
                    "output/**",
                    "outputs/**",
                    "artifacts/**",
                    "figures/**",
                    "**/*.json",
                    "*.md",
                    "*.txt",
                    "*.mmd",
                ],
            },
            "parallelism": parallelism,
        },
        "benchmark": {"enabled": benchmark},
        "report": {
            "formats": ["json", "html", "junit"],
            "artifacts": ["transcript", "logs"],
        },
    }


def run_evaluation(
    *,
    project_root: Path,
    skill_dir: str,
    agent: str,
    model: str | None,
    profile: str | None = None,
    case_files: list[str] | None = None,
    prompt: str | None = None,
    executable: str | None = None,
    must_contain: list[str] | None = None,
    must_not_contain: list[str] | None = None,
    parallelism: int = 1,
    iterations: int = 1,
    timeout_seconds: int = 1800,
    max_turns: int = 12,
    benchmark: bool = True,
    output_dir: str | None = None,
    extra_args: list[str] | None = None,
    validate_only: bool = False,
    collect_database_trace: bool = True,
    require_model_verification: bool = True,
    run_id: str | None = None,
    user_id: str = "local",
    task_name: str | None = None,
    progress_callback: Callable[[str, int, str], None] | None = None,
    event_callback: Callable[[str, str], None] | None = None,
    cancel_event: Event | None = None,
    task_id: str | None = None,
    client_task_id: str | None = None,
    run_llm_judge_enabled: bool = True,
    evaluation_type: str = "skill",
    selected_skills: list[str] | None = None,
    evaluator_id: str | None = None,
) -> dict[str, Any]:
    def progress(phase: str, percent: int, message: str) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise EvaluationCancelled("Evaluation was cancelled")
        if progress_callback is not None:
            progress_callback(phase, percent, message)
        if event_callback is not None:
            event_callback("phase", message)

    progress("preparing", 5, "Preparing isolated Skill workspace")
    user_id = _identity(user_id, field="user_id")
    if task_id is not None:
        task_id = _identity(task_id, field="task_id")
    if client_task_id is not None:
        client_task_id = _identity(client_task_id, field="client_task_id")
    source_skill = Path(skill_dir).resolve()
    if not (source_skill / "SKILL.md").is_file():
        raise FileNotFoundError(f"SKILL.md was not found under {source_skill}")
    if not case_files and not prompt:
        raise ValueError("Pass at least one --case or --prompt")
    selected_skills = selected_skills or [source_skill.name]
    evaluator = resolve_evaluator(
        project_root,
        evaluation_type=evaluation_type,
        evaluator_id=evaluator_id,
    )
    requested_agent = normalize_agent(agent)
    validate_evaluation_capabilities(
        requested_agent, require_model_selection=require_model_verification
    )
    capabilities = agent_capabilities(requested_agent)
    agent = backend_agent(requested_agent)
    resolved_profile = resolve_model_profile(
        project_root,
        profile_name=profile,
        model_override=model,
        agent=agent,
    )
    provider_model = resolved_profile.model
    model = resolved_profile.model_for_agent(agent)
    gateway_model = resolved_profile.gateway_model_for_agent(agent)
    if require_model_verification and not collect_database_trace:
        raise ValueError("require_model_verification=true requires collect_database_trace=true")
    if require_model_verification and not resolved_profile.api_base:
        raise ValueError(
            "Exact model verification requires a LiteLLM profile; native profiles have no "
            "run-scoped gateway trace"
        )
    if require_model_verification:
        health = database_health(project_root)
        if health.get("status") != "ok":
            detail = str(health.get("error") or health.get("status"))
            raise EvaluationInfrastructureError(
                f"PostgreSQL trace preflight failed: {detail}",
                category="postgresql_unavailable",
                retryable=_retryable_infrastructure_message(detail),
            )
    agent_executable = executable or default_agent_command(requested_agent, project_root)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    operation_id = _identity(task_id or run_id or uuid.uuid4().hex, field="task_id")
    canonical_task_id = operation_id
    owner = _slug(user_id or "local")
    task = _slug(task_name or source_skill.name)
    runs_root = Path(output_dir).resolve() if output_dir else project_root / "evaluation_results"
    result_root = runs_root / owner / task / f"{timestamp}__{operation_id}"
    if result_root.exists():
        raise FileExistsError(f"Task output already exists: {canonical_task_id}")
    staged_skill = result_root / "staging" / "skill"
    _copy_skill(source_skill, staged_skill)
    skill_quality = evaluate_skill_quality(source_skill)
    cases_dir = staged_skill / "evals" / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    staged_cases: list[Path] = []
    for index, item in enumerate(case_files or [], start=1):
        source = Path(item).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Case does not exist: {source}")
        target = cases_dir / f"{index:03d}-{source.name}"
        shutil.copy2(source, target)
        staged_cases.append(target.relative_to(staged_skill))
    if prompt:
        target = cases_dir / "cli-prompt.yaml"
        _generated_case(
            target,
            prompt,
            must_contain or [],
            must_not_contain or [],
        )
        staged_cases.append(target.relative_to(staged_skill))

    runtime_binary = find_multica_runtime(project_root)
    skill_up = find_skill_up(project_root)
    additional_skills = []
    for index, selected_name in enumerate(selected_skills, start=1):
        relative = Path("skills") / f"{index:02d}-{_slug(selected_name)}"
        if (staged_skill / relative / "SKILL.md").is_file():
            additional_skills.append((relative.as_posix(), selected_name))
    eval_config = build_eval_config(
        agent=agent,
        model=model,
        executable=agent_executable,
        runtime_binary=runtime_binary,
        skill_name=_slug(source_skill.name),
        case_paths=staged_cases,
        parallelism=parallelism,
        timeout_seconds=timeout_seconds,
        max_turns=max_turns,
        benchmark=benchmark,
        extra_args=[*resolved_profile.agent_args, *(extra_args or [])],
        additional_skills=additional_skills,
    )
    eval_path = staged_skill / "evals" / "eval.yaml"
    eval_path.write_text(
        yaml.safe_dump(eval_config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    output = result_root / "skill-up"
    env = os.environ.copy()
    env.update(resolved_profile.environment)
    if resolved_profile.api_base and agent == "codex":
        # Do not inherit the operator's global Codex plugins/MCP servers into an
        # evaluation. They add unrelated tools and can consume most of a model's
        # context before the task starts. Project-local injected Skills remain
        # available from the isolated Skill-Up workspace.
        codex_home = result_root / "runtime" / "codex-home"
        codex_home.mkdir(parents=True, exist_ok=True)
        env["CODEX_HOME"] = str(codex_home)
    if resolved_profile.api_base and agent == "claude":
        claude_config = result_root / "runtime" / "claude-config"
        claude_config.mkdir(parents=True, exist_ok=True)
        env["CLAUDE_CONFIG_DIR"] = str(claude_config)
    if resolved_profile.api_base and agent == "codebuddy":
        codebuddy_config = result_root / "runtime" / "codebuddy-config"
        write_codebuddy_profile_config(codebuddy_config / "models.json", resolved_profile)
        env["CODEBUDDY_CONFIG_DIR"] = str(codebuddy_config)
    if resolved_profile.api_base and agent == "openclaw":
        openclaw_config = result_root / "runtime" / "openclaw.json"
        openclaw_workspace = result_root / "runtime" / "openclaw-workspace"
        _copy_skill(staged_skill, openclaw_workspace / "skills" / _slug(source_skill.name))
        write_openclaw_profile_config(
            openclaw_config, resolved_profile, workspace=openclaw_workspace
        )
        env["OPENCLAW_CONFIG_PATH"] = str(openclaw_config)
        openclaw_state = result_root / "runtime" / "openclaw-state"
        openclaw_state.mkdir(parents=True, exist_ok=True)
        env["OPENCLAW_STATE_DIR"] = str(openclaw_state)
    env["AGENT_EVAL_RUN_ID"] = operation_id
    env["AGENT_EVAL_TASK_ID"] = canonical_task_id
    env["AGENT_EVAL_USER_ID"] = user_id
    env["AGENT_EVAL_AGENT_EXECUTABLE"] = agent_executable
    env["AGENT_EVAL_REQUESTED_AGENT"] = requested_agent
    env["AGENT_EVAL_SUBAGENT_MODEL"] = provider_model

    progress("validating", 15, "Validating Skill-Up configuration")
    validation = _execute_process(
        [str(skill_up), "validate", str(eval_path)],
        cwd=project_root,
        env=env,
        cancel_event=cancel_event,
        event_callback=event_callback,
    )
    (result_root / "validate.stdout.log").write_text(validation.stdout, encoding="utf-8")
    (result_root / "validate.stderr.log").write_text(validation.stderr, encoding="utf-8")
    if validation.returncode:
        raise RuntimeError(f"skill-up validation failed; see {result_root}")

    if validate_only:
        summary = {
            "run_id": operation_id,
            "task_id": canonical_task_id,
            "client_task_id": client_task_id,
            "user_id": user_id,
            "task_name": task_name or source_skill.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "agent": requested_agent,
            "agent_backend": agent,
            "agent_capabilities": capabilities,
            "model": model,
            "model_profile": resolved_profile.name,
            "provider_model": provider_model,
            "skill": str(source_skill),
            "skills": selected_skills,
            "evaluation_type": evaluation_type,
            "evaluation": {
                "evaluator_id": evaluator.id,
                "evaluator_version": str(evaluator.version),
                "api_version": evaluator.api_version,
            },
            "result_dir": str(result_root),
            "validated": True,
            "skill_up_exit_code": 0,
            "iterations": 0,
            "skill_quality": skill_quality,
            "eval_config_file": str(eval_path),
            "eval_config_generated_by": "agent_eval.runner.build_eval_config",
            "results": [],
        }
        (result_root / "evaluation-report.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary

    trace_key = None
    trace_key_error: str | None = None
    if collect_database_trace and resolved_profile.api_base:
        try:
            trace_key = create_trace_key(
                resolved_profile.api_base,
                gateway_model,
                operation_id,
                master_key=resolve_config_secret(project_root, "LITELLM_MASTER_KEY"),
            )
            if trace_key is not None:
                for key_name in CREDENTIAL_ENV_NAMES:
                    env[key_name] = trace_key.key
                if resolved_profile.api_key_env:
                    env[resolved_profile.api_key_env] = trace_key.key
        except Exception as exc:
            trace_key_error = str(exc)
            trace_key = None
            if require_model_verification:
                raise EvaluationInfrastructureError(
                    f"Exact LiteLLM trace-key creation failed: {exc}",
                    category="trace_key_unavailable",
                    retryable=isinstance(exc, TraceKeyError) and exc.retryable,
                ) from exc
    if require_model_verification and trace_key is None:
        raise EvaluationInfrastructureError(
            "Exact model verification requires LITELLM_MASTER_KEY and a run-scoped virtual key",
            category="trace_key_not_configured",
            retryable=False,
        )

    command = [
        str(skill_up),
        "run",
        str(eval_path),
        "--output-dir",
        str(output),
        "--iteration",
        str(iterations),
        "--format",
        "html",
        "--format",
        "junit",
    ]
    evaluation_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    progress("running", 25, "Agent evaluation is running")
    interaction_stop = Event()
    interaction_thread: Thread | None = None
    if event_callback is not None and collect_database_trace and trace_key is not None:
        def monitor_model_interactions() -> None:
            seen: set[tuple[str, str, int]] = set()
            while True:
                stopping = interaction_stop.wait(1.0)
                try:
                    live_rows = fetch_model_interactions(
                        project_root,
                        started_at=evaluation_started_at,
                        finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        model=provider_model,
                        key_alias=trace_key.alias,
                    )
                    for row in live_rows:
                        identity = (
                            str(row.get("request_id") or ""),
                            str(row.get("status") or ""),
                            int(row.get("total_tokens") or 0),
                        )
                        if identity in seen:
                            continue
                        seen.add(identity)
                        request_content = row.get("messages") or row.get("proxy_server_request")
                        response_content = row.get("response")
                        if request_content:
                            event_callback(
                                "model_request",
                                json.dumps(request_content, ensure_ascii=False, default=str),
                            )
                        if response_content:
                            event_callback(
                                "model_response",
                                json.dumps(response_content, ensure_ascii=False, default=str),
                            )
                except Exception:
                    # Live display is best effort; final trace collection remains authoritative.
                    pass
                if stopping:
                    return

        interaction_thread = Thread(
            target=monitor_model_interactions,
            name=f"agent-eval-model-trace-{operation_id[:8]}",
            daemon=True,
        )
    resilience_proxy = None
    gateway_resilience: dict[str, Any] = {"status": "not_used"}
    if resolved_profile.api_base and agent in {"claude", "codex", "codebuddy", "openclaw"}:
        resilience_proxy = CodeBuddyCompatibilityProxy(
            resolved_profile.api_base,
            timeout=timeout_seconds,
            forced_model=gateway_model,
            strip_tools_after_result=False,
            translate_protocols=agent in {"claude", "codex"},
        )
        try:
            resilience_proxy.start()
        except Exception:
            try:
                delete_trace_key(trace_key)
            except Exception:
                pass
            raise
        gateway_resilience = {"status": "active"}
        if agent == "claude":
            env["ANTHROPIC_BASE_URL"] = resilience_proxy.anthropic_base_url
        elif agent == "codex":
            eval_config["engine"]["custom"]["local"]["args"].extend([
                "--extra-arg", "-c", "--extra-arg",
                f'model_providers.litellm.base_url="{resilience_proxy.openai_base_url}"',
            ])
            eval_path.write_text(yaml.safe_dump(eval_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        elif agent == "codebuddy":
            codebuddy_config = Path(env["CODEBUDDY_CONFIG_DIR"])
            write_codebuddy_profile_config(
                codebuddy_config / "models.json", resolved_profile, endpoint=resilience_proxy.url
            )
        elif agent == "openclaw":
            write_openclaw_profile_config(
                Path(env["OPENCLAW_CONFIG_PATH"]),
                resolved_profile,
                workspace=openclaw_workspace,
                api_base_override=resilience_proxy.openai_base_url,
            )
            # JustDo intentionally ignores the evaluator's temporary OpenClaw
            # config and imports this request-scoped provider instead. Point
            # that provider at the same resilience proxy so JustDo receives
            # retries and the evaluator can retain the real upstream failure.
            env["AGENT_EVAL_PROVIDER_BASE_URL"] = resilience_proxy.openai_base_url
    if interaction_thread is not None:
        interaction_thread.start()
    try:
        completed = _execute_process(
            command,
            cwd=project_root,
            env=env,
            cancel_event=cancel_event,
            event_callback=event_callback,
        )
    except Exception:
        # Cancellation or a local process-launch failure must not leave the
        # run-scoped credential alive until its one-hour TTL.
        try:
            delete_trace_key(trace_key)
        except Exception:
            pass
        raise
    finally:
        interaction_stop.set()
        if interaction_thread is not None:
            interaction_thread.join(timeout=6)
        if resilience_proxy is not None:
            gateway_resilience = {"status": "completed", **resilience_proxy.stats()}
            resilience_proxy.close()
    evaluation_finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    (result_root / "skill-up.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (result_root / "skill-up.stderr.log").write_text(completed.stderr, encoding="utf-8")
    iteration_dirs = sorted(path for path in output.glob("iteration-*") if path.is_dir())
    results = []
    for iteration in iteration_dirs:
        result_file = iteration / "result.json"
        if result_file.is_file():
            result = json.loads(result_file.read_text(encoding="utf-8"))
            results.append(attach_session_evidence(iteration, result))
    scores = aggregate_scores(results)
    scores["skill_quality_score"] = skill_quality["score"]
    database_trace: dict[str, Any] = {"status": "disabled"}
    interactions: list[dict[str, Any]] = []
    model_verification: dict[str, Any] = {
        "status": "not_requested", "verified": None, "expected_model": provider_model
    }
    trace_file: str | None = None
    if collect_database_trace:
        progress("collecting_trace", 85, "Collecting model interaction records")
        try:
            interactions: list[dict[str, Any]] = []
            # LiteLLM writes SpendLogs asynchronously. Slower providers and a
            # remote PostgreSQL instance can lag several seconds behind a
            # successfully completed Agent process.
            interactions = wait_for_model_interactions(
                    project_root,
                    started_at=evaluation_started_at,
                    finished_at=evaluation_finished_at,
                    model=provider_model,
                    key_alias=trace_key.alias if trace_key else None,
                )
            database_trace = summarize_model_interactions(interactions, exact=trace_key is not None)
            database_trace["collection_policy"] = "bounded_60s_wait_for_stable_rows_not_lossless_guarantee"
            if trace_key_error:
                database_trace["exact_correlation_error"] = trace_key_error
            accepted_groups = [gateway_model]
            model_verification = verify_requested_model(
                interactions,
                expected_model=provider_model,
                accepted_model_groups=accepted_groups,
                exact=trace_key is not None,
            )
            scores["model_trace_score"] = database_trace["model_call_success_rate"]
            scores["model_verification_score"] = 100 if model_verification["verified"] else 0
            trace_path = result_root / "model-interactions.json"
            trace_path.write_text(
                json.dumps(interactions, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            trace_file = str(trace_path)
        except Exception as exc:
            database_trace = {"status": "unavailable", "error": str(exc)}
            model_verification = {
                "status": "unverified", "verified": False,
                "expected_model": provider_model, "reason": "database_trace_unavailable",
                "error": str(exc),
            }
            scores["model_trace_score"] = None
            scores["model_verification_score"] = 0

    verification_failed = bool(
        collect_database_trace
        and require_model_verification
        and model_verification.get("verified") is not True
    )
    combined_failure_text = "\n".join(
        str(value or "") for value in (
            completed.stdout,
            completed.stderr,
            json.dumps(results, ensure_ascii=False, default=str),
            json.dumps(gateway_resilience, ensure_ascii=False, default=str),
        )
    )
    failure = classify_evaluation_failure(combined_failure_text, completed.returncode)
    # Skill-Up exits nonzero for assertion FAIL too. A scored negative control
    # is valid evidence, unlike an ERROR/TIMEOUT or a missing result.
    cases_executed = cases_completed(results)
    evaluation_status = "completed" if cases_executed and not verification_failed else "failed"
    if evaluation_status == "completed":
        failure = None
    elif isinstance(gateway_resilience.get("last_failure"), dict):
        proxy_failure = gateway_resilience["last_failure"]
        failure = describe_evaluation_failure(
            str(proxy_failure.get("detail") or ""),
            returncode=completed.returncode or 1,
            status_code=int(proxy_failure["status_code"]),
            component="agent",
        )
    if verification_failed and failure is None:
        failure = {
            "category": "model_verification_failed",
            "retryable": database_trace.get("status") == "unavailable",
            "summary": model_verification.get("reason") or "Exact model verification failed",
        }

    trace_key_cleanup: dict[str, Any] = {"status": "not_created"}
    if trace_key is not None:
        try:
            delete_trace_key(trace_key)
            trace_key_cleanup = {"status": "deleted", "alias": trace_key.alias}
        except Exception as exc:
            # The key expires after one hour, so cleanup failure does not alter
            # attribution, but it must remain visible to operators.
            trace_key_cleanup = {
                "status": "delete_failed",
                "alias": trace_key.alias,
                "error": str(exc),
            }

    progress("scoring", 92, "Calculating rule and LLM evaluation scores")
    process_metrics = collect_process_metrics(results, database_trace)
    supplement_database_tool_metrics(process_metrics, interactions)
    process_metrics["total_duration_ms"] = scores.get("total_duration_ms", 0)
    skill_usage = collect_skill_read_evidence(interactions, selected_skills)
    scoring_config = load_scoring_config(project_root)
    plugin_evaluation = evaluator.evaluate(
        context=EvaluationContext(
            run_id=operation_id,
            task_id=canonical_task_id,
            evaluation_type=evaluation_type,
            agent=agent,
            requested_model=model,
            skill_name=source_skill.name,
            selected_skills=tuple(selected_skills),
            skill_md=(source_skill / "SKILL.md").read_text(encoding="utf-8")[:30000],
        ),
        evidence=EvaluationEvidence(
            deterministic_scores=scores,
            process_metrics=process_metrics,
            skill_usage=skill_usage,
            skill_quality=skill_quality,
            results=results,
            interactions=interactions,
        ),
        scoring_config=scoring_config,
    )
    if not isinstance(plugin_evaluation, PluginEvaluation):
        raise TypeError(
            f"Evaluator {evaluator.id} must return PluginEvaluation from evaluator API v1"
        )
    if not run_llm_judge_enabled:
        llm_judge = {"status": "disabled_by_request"}
    elif evaluation_status != "completed":
        llm_judge = {
            "status": "skipped_due_to_execution_failure",
            "reason": "Agent execution did not produce a valid result for LLM judging",
            "failure": failure,
        }
    else:
        llm_judge = run_llm_judge(
            project_root=project_root,
            scoring_config=scoring_config,
            evidence=plugin_evaluation.llm_evidence,
            system_prompt=plugin_evaluation.judge_system_prompt,
        )
    scoring = combine_dimensions(
        rule_dimensions=plugin_evaluation.rule_dimensions,
        llm_judge=llm_judge,
        config=scoring_config,
    )
    scoring["status"] = "completed" if not run_llm_judge_enabled or llm_judge.get("status") == "completed" else "partial"
    scoring["valid_for_ranking"] = evaluation_status == "completed" and scoring["status"] == "completed"
    scoring["diagnostic_only"] = not scoring["valid_for_ranking"]
    scoring["extensions"] = plugin_evaluation.extensions
    scores["overall_score"] = scoring["overall_score"]
    scores["result_dimension_score"] = scoring["dimensions"]["result"]["score"]
    scores["process_dimension_score"] = scoring["dimensions"]["process"]["score"]
    scores["skill_quality_dimension_score"] = scoring["dimensions"]["skill_quality"]["score"]
    agent_contract = assess_agent_contract(
        agent=agent,
        requested_model=model,
        process_metrics=process_metrics,
        skill_up_exit_code=0 if cases_executed else completed.returncode or 1,
    )
    summary = {
        "run_id": operation_id,
        "task_id": canonical_task_id,
        "client_task_id": client_task_id,
        "user_id": user_id,
        "task_name": task_name or source_skill.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": evaluation_status,
        "agent": requested_agent,
        "agent_backend": agent,
        "agent_capabilities": capabilities,
        "model": model,
        "model_profile": resolved_profile.name,
        "provider_model": provider_model,
        "gateway_model": gateway_model,
        "skill": str(source_skill),
        "skills": selected_skills,
        "evaluation_type": evaluation_type,
        "evaluation": {
            "evaluator_id": evaluator.id,
            "evaluator_version": str(evaluator.version),
            "api_version": evaluator.api_version,
        },
        "result_dir": str(result_root),
        "skill_up_exit_code": completed.returncode,
        "iterations": len(results),
        "scores": scores,
        "skill_quality": skill_quality,
        "process_metrics": process_metrics,
        "skill_usage": skill_usage,
        "scoring": scoring,
        "agent_contract": agent_contract,
        "database_trace": database_trace,
        "model_verification": model_verification,
        "require_model_verification": require_model_verification,
        "database_trace_file": trace_file,
        "gateway_resilience": gateway_resilience,
        "trace_key_cleanup": trace_key_cleanup,
        "failure": failure,
        "eval_config_file": str(eval_path),
        "eval_config_generated_by": "agent_eval.runner.build_eval_config",
        "results": results,
    }
    (result_root / "evaluation-report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    progress(
        "completed" if evaluation_status == "completed" else "failed",
        100,
        "Evaluation completed" if evaluation_status == "completed" else "Model verification failed",
    )
    return summary
