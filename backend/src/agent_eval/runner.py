from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from agent_eval.runtime import (
    default_agent_command,
    find_multica_runtime,
    find_skill_up,
    normalize_agent,
    skill_target,
)


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._") or "run"


def _copy_skill(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            ".git", ".runtime", ".tools", "runs", "__pycache__", "*.pyc"
        ),
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


def build_eval_config(
    *,
    agent: str,
    model: str,
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
    model: str,
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
) -> dict[str, Any]:
    source_skill = Path(skill_dir).resolve()
    if not (source_skill / "SKILL.md").is_file():
        raise FileNotFoundError(f"SKILL.md was not found under {source_skill}")
    if not case_files and not prompt:
        raise ValueError("Pass at least one --case or --prompt")
    agent = normalize_agent(agent)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    unique = uuid.uuid4().hex[:8]
    runs_root = Path(output_dir).resolve() if output_dir else project_root / "runs"
    result_root = runs_root / (
        f"{timestamp}__{_slug(source_skill.name)}__{_slug(agent)}-{_slug(model)}__{unique}"
    )
    staged_skill = result_root / "staging" / "skill"
    _copy_skill(source_skill, staged_skill)
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
        executable=executable or default_agent_command(agent),
        runtime_binary=runtime_binary,
        skill_name=_slug(source_skill.name),
        case_paths=staged_cases,
        parallelism=parallelism,
        timeout_seconds=timeout_seconds,
        max_turns=max_turns,
        benchmark=benchmark,
        extra_args=extra_args or [],
    )
    eval_path = staged_skill / "evals" / "eval.yaml"
    eval_path.write_text(
        yaml.safe_dump(eval_config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    output = result_root / "skill-up"
    env = os.environ.copy()
    env["AGENT_EVAL_RUN_ID"] = result_root.name
    env["AGENT_EVAL_AGENT_EXECUTABLE"] = executable or default_agent_command(agent)

    validation = subprocess.run(
        [str(skill_up), "validate", str(eval_path)],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    (result_root / "validate.stdout.log").write_text(validation.stdout, encoding="utf-8")
    (result_root / "validate.stderr.log").write_text(validation.stderr, encoding="utf-8")
    if validation.returncode:
        raise RuntimeError(f"skill-up validation failed; see {result_root}")

    if validate_only:
        summary = {
            "run_id": result_root.name,
            "agent": agent,
            "model": model,
            "skill": str(source_skill),
            "result_dir": str(result_root),
            "validated": True,
            "skill_up_exit_code": 0,
            "iterations": 0,
            "results": [],
        }
        (result_root / "evaluation-report.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary

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
    completed = subprocess.run(
        command,
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    (result_root / "skill-up.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (result_root / "skill-up.stderr.log").write_text(completed.stderr, encoding="utf-8")
    iteration_dirs = sorted(path for path in output.glob("iteration-*") if path.is_dir())
    results = []
    for iteration in iteration_dirs:
        result_file = iteration / "result.json"
        if result_file.is_file():
            results.append(json.loads(result_file.read_text(encoding="utf-8")))
    summary = {
        "run_id": result_root.name,
        "agent": agent,
        "model": model,
        "skill": str(source_skill),
        "result_dir": str(result_root),
        "skill_up_exit_code": completed.returncode,
        "iterations": len(results),
        "scores": aggregate_scores(results),
        "results": results,
    }
    (result_root / "evaluation-report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
