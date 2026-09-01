from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from agent_eval.runner import run_evaluation
from agent_eval.model_config import describe_model_config
from agent_eval.runtime import (
    SUPPORTED_AGENTS,
    default_agent_command,
    find_multica_runtime,
    find_skill_up,
)


# backend/src/agent_eval/cli.py -> parents[2] = backend
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-eval")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Evaluate one Skill with a chosen Agent and model")
    run.add_argument("--skill", required=True)
    run.add_argument("--agent", required=True)
    run.add_argument("--model", help="Override the model from the selected profile")
    run.add_argument("--profile", help="Model profile from config/models.yaml")
    run.add_argument("--case", action="append", default=[])
    run.add_argument("--prompt")
    run.add_argument("--must-contain", action="append", default=[])
    run.add_argument("--must-not-contain", action="append", default=[])
    run.add_argument("--agent-executable")
    run.add_argument("--agent-arg", action="append", default=[])
    run.add_argument("--parallelism", type=int, default=1)
    run.add_argument("--iterations", type=int, default=1)
    run.add_argument("--timeout", type=int, default=1800)
    run.add_argument("--max-turns", type=int, default=12)
    run.add_argument("--output-dir")
    run.add_argument("--benchmark", action=argparse.BooleanOptionalAction, default=True)
    run.add_argument("--validate-only", action="store_true")
    run.add_argument(
        "--database-trace",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Collect matching LiteLLM interaction rows from PostgreSQL",
    )

    commands.add_parser("doctor", help="Check the local skill-up and Multica runtime")
    commands.add_parser("agents", help="List Multica Agent backends and local CLI discovery")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "agents":
        result = []
        for agent in SUPPORTED_AGENTS:
            command = default_agent_command(agent)
            result.append(
                {
                    "agent": agent,
                    "default_command": command,
                    "detected_executable": shutil.which(command),
                }
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "doctor":
        result = {
            "skill_up": str(find_skill_up(PROJECT_ROOT)),
            "multica_eval_runtime": str(find_multica_runtime(PROJECT_ROOT)),
            "login_required": False,
            "database_required": False,
            "litellm_required": True,
            "model_config": describe_model_config(PROJECT_ROOT),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    result = run_evaluation(
        project_root=PROJECT_ROOT,
        skill_dir=args.skill,
        agent=args.agent,
        model=args.model,
        profile=args.profile,
        case_files=args.case,
        prompt=args.prompt,
        executable=args.agent_executable,
        must_contain=args.must_contain,
        must_not_contain=args.must_not_contain,
        parallelism=args.parallelism,
        iterations=args.iterations,
        timeout_seconds=args.timeout,
        max_turns=args.max_turns,
        benchmark=args.benchmark,
        output_dir=args.output_dir,
        extra_args=args.agent_arg,
        validate_only=args.validate_only,
        collect_database_trace=args.database_trace,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["skill_up_exit_code"] == 0 else 1)


if __name__ == "__main__":
    main()
