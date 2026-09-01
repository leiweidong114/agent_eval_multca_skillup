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
from threading import Event
from collections.abc import Callable

import psutil

import yaml

from agent_eval.database import (
    fetch_model_interactions,
    summarize_model_interactions,
    verify_requested_model,
)
from agent_eval.model_config import (
    resolve_config_secret,
    resolve_model_profile,
    write_openclaw_profile_config,
    write_codebuddy_profile_config,
)
from agent_eval.skill_quality import evaluate_skill_quality
from agent_eval.litellm_trace import create_trace_key, delete_trace_key
from agent_eval.agent_contract import assess_agent_contract
from agent_eval.llm_judge import run_llm_judge
from agent_eval.scoring import (
    calculate_rule_dimensions,
    collect_process_metrics,
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


def _execute_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    cancel_event: Event | None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.5)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if cancel_event is None or not cancel_event.is_set():
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
            process.communicate()
            raise EvaluationCancelled("Evaluation was cancelled")


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
                "collect_artifacts": ["output/**", "outputs/**", "artifacts/**", "**/*.json"],
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
    cancel_event: Event | None = None,
    task_id: str | None = None,
    client_task_id: str | None = None,
    run_llm_judge_enabled: bool = True,
) -> dict[str, Any]:
    def progress(phase: str, percent: int, message: str) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise EvaluationCancelled("Evaluation was cancelled")
        if progress_callback is not None:
            progress_callback(phase, percent, message)

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
    agent_executable = executable or default_agent_command(requested_agent)
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
    )
    eval_path = staged_skill / "evals" / "eval.yaml"
    eval_path.write_text(
        yaml.safe_dump(eval_config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    output = result_root / "skill-up"
    env = os.environ.copy()
    if agent == "claude":
        env.pop("ANTHROPIC_API_KEY", None)
    env.update(resolved_profile.environment)
    if agent == "claude":
        claude_config = result_root / "runtime" / "claude-config"
        claude_config.mkdir(parents=True, exist_ok=True)
        env["CLAUDE_CONFIG_DIR"] = str(claude_config)
    if agent == "codebuddy":
        codebuddy_config = result_root / "runtime" / "codebuddy-config"
        write_codebuddy_profile_config(codebuddy_config / "models.json", resolved_profile)
        env["CODEBUDDY_CONFIG_DIR"] = str(codebuddy_config)
    if agent == "openclaw":
        openclaw_config = result_root / "runtime" / "openclaw.json"
        write_openclaw_profile_config(openclaw_config, resolved_profile)
        env["OPENCLAW_CONFIG_PATH"] = str(openclaw_config)
    env["AGENT_EVAL_RUN_ID"] = operation_id
    env["AGENT_EVAL_TASK_ID"] = canonical_task_id
    env["AGENT_EVAL_USER_ID"] = user_id
    env["AGENT_EVAL_AGENT_EXECUTABLE"] = agent_executable

    progress("validating", 15, "Validating Skill-Up configuration")
    validation = _execute_process(
        [str(skill_up), "validate", str(eval_path)],
        cwd=project_root,
        env=env,
        cancel_event=cancel_event,
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
                provider_model,
                operation_id,
                master_key=resolve_config_secret(project_root, "LITELLM_MASTER_KEY"),
            )
            if trace_key is not None:
                for key_name in (
                    "LITELLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN", "MINIMAX_API_KEY",
                ):
                    env[key_name] = trace_key.key
        except Exception as exc:
            trace_key_error = str(exc)
            trace_key = None

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
    completed = _execute_process(
        command,
        cwd=project_root,
        env=env,
        cancel_event=cancel_event,
    )
    evaluation_finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    (result_root / "skill-up.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (result_root / "skill-up.stderr.log").write_text(completed.stderr, encoding="utf-8")
    iteration_dirs = sorted(path for path in output.glob("iteration-*") if path.is_dir())
    results = []
    for iteration in iteration_dirs:
        result_file = iteration / "result.json"
        if result_file.is_file():
            results.append(json.loads(result_file.read_text(encoding="utf-8")))
    scores = aggregate_scores(results)
    scores["skill_quality_score"] = skill_quality["score"]
    database_trace: dict[str, Any] = {"status": "disabled"}
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
            for attempt in range(8):
                interactions = fetch_model_interactions(
                    project_root,
                    started_at=evaluation_started_at,
                    finished_at=evaluation_finished_at,
                    model=provider_model,
                    key_alias=trace_key.alias if trace_key else None,
                )
                if interactions or attempt == 7:
                    break
                time.sleep(2)
            database_trace = summarize_model_interactions(interactions, exact=trace_key is not None)
            if trace_key_error:
                database_trace["exact_correlation_error"] = trace_key_error
            agent_model = resolved_profile.model_for_agent(agent)
            accepted_groups = [agent_model.removeprefix("custom-local:")]
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
    evaluation_status = "failed" if completed.returncode or verification_failed else "completed"

    progress("scoring", 92, "Calculating rule and LLM evaluation scores")
    process_metrics = collect_process_metrics(results, database_trace)
    process_metrics["total_duration_ms"] = scores.get("total_duration_ms", 0)
    scoring_config = load_scoring_config(project_root)
    rule_dimensions = calculate_rule_dimensions(
        scores=scores,
        process=process_metrics,
        skill_quality=skill_quality,
        config=scoring_config,
    )
    llm_evidence = {
        "task": {
            "task_id": canonical_task_id,
            "agent": agent,
            "requested_model": model,
            "skill": source_skill.name,
        },
        "deterministic_scores": scores,
        "process_metrics": process_metrics,
        "skill_quality_rules": skill_quality,
        "skill_md": (source_skill / "SKILL.md").read_text(encoding="utf-8")[:30000],
        "skill_up_results": results,
    }
    llm_judge = (
        run_llm_judge(
            project_root=project_root,
            scoring_config=scoring_config,
            evidence=llm_evidence,
        )
        if run_llm_judge_enabled
        else {"status": "disabled_by_request"}
    )
    scoring = combine_dimensions(
        rule_dimensions=rule_dimensions,
        llm_judge=llm_judge,
        config=scoring_config,
    )
    scores["overall_score"] = scoring["overall_score"]
    scores["result_dimension_score"] = scoring["dimensions"]["result"]["score"]
    scores["process_dimension_score"] = scoring["dimensions"]["process"]["score"]
    scores["skill_quality_dimension_score"] = scoring["dimensions"]["skill_quality"]["score"]
    agent_contract = assess_agent_contract(
        agent=agent,
        requested_model=model,
        process_metrics=process_metrics,
        skill_up_exit_code=completed.returncode,
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
        "skill": str(source_skill),
        "result_dir": str(result_root),
        "skill_up_exit_code": completed.returncode,
        "iterations": len(results),
        "scores": scores,
        "skill_quality": skill_quality,
        "process_metrics": process_metrics,
        "scoring": scoring,
        "agent_contract": agent_contract,
        "database_trace": database_trace,
        "model_verification": model_verification,
        "require_model_verification": require_model_verification,
        "database_trace_file": trace_file,
        "eval_config_file": str(eval_path),
        "eval_config_generated_by": "agent_eval.runner.build_eval_config",
        "results": results,
    }
    (result_root / "evaluation-report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        delete_trace_key(trace_key)
    except Exception:
        pass
    progress(
        "completed" if evaluation_status == "completed" else "failed",
        100,
        "Evaluation completed" if evaluation_status == "completed" else "Model verification failed",
    )
    return summary
