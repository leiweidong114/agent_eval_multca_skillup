from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
import shutil
import sys
from pathlib import Path

from .config import Experiment, load_experiment
from .adapters import resolve_executable
from .runner import run_experiment
from .models import AdapterResult, RunRecord
from .reporting import write_reports
from .scoring import score_result
from .web_cli import add_platform_parser, run_platform


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maeval",
        description="Evaluate direct models and model-plus-agent systems.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_platform_parser(subparsers)
    for name in ("doctor", "run", "show", "rescore"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", required=True, type=Path)
        if name == "rescore":
            command.add_argument(
                "--results",
                required=True,
                type=Path,
                help="Path to an existing results.jsonl file.",
            )
        command.add_argument(
            "--candidate",
            action="append",
            help="Only include this candidate id; may be repeated.",
        )
        command.add_argument(
            "--task",
            action="append",
            help="Only include this task id; may be repeated.",
        )
        command.add_argument(
            "--repeats",
            type=int,
            help="Override the configured repeat count.",
        )
    return parser


def _doctor(experiment: Experiment) -> int:
    issues: list[str] = []
    adapters = {item.adapter for item in experiment.candidates}
    if any(name.startswith("claude_cli") for name in adapters):
        if not resolve_executable("claude"):
            issues.append("claude executable not found")
    if "openclaw_agent" in adapters and not resolve_executable("openclaw"):
        issues.append("openclaw executable not found")
    for candidate in experiment.candidates:
        if candidate.adapter == "direct_http":
            if not candidate.api_key_env:
                issues.append(f"{candidate.id}: api_key_env is missing")
            elif not os.environ.get(candidate.api_key_env):
                issues.append(
                    f"{candidate.id}: environment variable "
                    f"{candidate.api_key_env} is not set"
                )
    print(f"Experiment: {experiment.id}")
    print(f"Candidates: {len(experiment.candidates)}")
    print(f"Tasks: {len(experiment.tasks)}")
    print(f"Repeats: {experiment.repeats}")
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        return 1
    print("Doctor checks passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "platform":
        return run_platform(args)
    try:
        experiment = load_experiment(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.candidate:
        selected = set(args.candidate)
        experiment = replace(
            experiment,
            candidates=tuple(
                item for item in experiment.candidates if item.id in selected
            ),
        )
        missing = selected - {item.id for item in experiment.candidates}
        if missing:
            print(f"Unknown candidates: {sorted(missing)}", file=sys.stderr)
            return 2
    if args.task:
        selected = set(args.task)
        experiment = replace(
            experiment,
            tasks=tuple(item for item in experiment.tasks if item.id in selected),
        )
        missing = selected - {item.id for item in experiment.tasks}
        if missing:
            print(f"Unknown tasks: {sorted(missing)}", file=sys.stderr)
            return 2
    if args.repeats is not None:
        if args.repeats < 1:
            print("--repeats must be at least 1", file=sys.stderr)
            return 2
        experiment = replace(experiment, repeats=args.repeats)
    if args.command == "doctor":
        return _doctor(experiment)
    if args.command == "show":
        print(f"id={experiment.id}")
        for candidate in experiment.candidates:
            print(
                f"candidate={candidate.id} adapter={candidate.adapter} "
                f"model={candidate.model}"
            )
        for task in experiment.tasks:
            print(f"task={task.id} kind={task.kind} tags={','.join(task.tags)}")
        return 0
    if args.command == "rescore":
        results_path = args.results.resolve()
        rows = [
            json.loads(line)
            for line in results_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        task_by_id = {task.id: task for task in experiment.tasks}
        records: list[RunRecord] = []
        for row in rows:
            task = task_by_id.get(row["task_id"])
            if task is None:
                print(
                    f"Task {row['task_id']!r} is missing from config.",
                    file=sys.stderr,
                )
                return 2
            adapter_result = AdapterResult(
                ok=bool(row["adapter_ok"]),
                text=str(row["response_text"]),
                error=row.get("error"),
            )
            workdir = Path(row["workdir"]) if row.get("workdir") else None
            score = score_result(task, adapter_result, workdir)
            row["passed"] = bool(row["adapter_ok"]) and score.passed
            row["score"] = score.score if row["adapter_ok"] else 0.0
            row["score_detail"] = score.detail
            row["test_duration_ms"] = score.test_duration_ms
            records.append(RunRecord(**row))
        backup = results_path.with_name("results.pre-rescore.jsonl")
        if not backup.exists():
            shutil.copy2(results_path, backup)
        write_reports(records, results_path.parent)
        print(f"Rescored {len(records)} runs: {results_path.parent}")
        return 0
    output_dir, records = run_experiment(experiment)
    passed = sum(record.passed for record in records)
    print(f"Completed {len(records)} runs; passed={passed}.")
    print(f"Results: {output_dir}")
    return 0 if passed == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
