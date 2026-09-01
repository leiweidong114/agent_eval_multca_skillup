from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_eval.runner import run_evaluation
from agent_eval.agent_contract import describe_agent_contract
from agent_eval.database import (
    fetch_model_interactions,
    summarize_model_interactions,
    verify_requested_model,
)
from agent_eval.model_config import (
    describe_model_config,
    resolve_model_profile,
    write_codebuddy_profile_config,
    write_openclaw_profile_config,
)
from agent_eval.runtime import (
    SUPPORTED_AGENTS,
    agent_capabilities,
    backend_agent,
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
    run.add_argument("--user", "--user-id", dest="user_id", default="local", help="Archive owner directory")
    run.add_argument("--task-name", help="Archive task directory; defaults to Skill name")
    run.add_argument("--task-id", help="Optional caller-provided unique task id")
    run.add_argument("--client-task-id", help="Optional business-side correlation id")
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
    run.add_argument(
        "--require-model-verification",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require exact PostgreSQL proof that the requested model was called",
    )
    run.add_argument(
        "--llm-judge",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run the configured LiteLLM judge in addition to deterministic rules",
    )

    commands.add_parser("doctor", help="Check the local skill-up and Multica runtime")
    commands.add_parser("agents", help="List Multica Agent backends and local CLI discovery")
    check = commands.add_parser(
        "check-agent", help="Test one Agent/model connection without running an evaluation"
    )
    check.add_argument("--agent", required=True)
    check.add_argument("--profile", required=True)
    check.add_argument("--model", help="Override the profile model")
    check.add_argument("--agent-executable")
    check.add_argument("--timeout", type=int, default=120)
    check.add_argument(
        "--database-verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Confirm the requested model in PostgreSQL after the connectivity probe",
    )
    return parser


def _check_agent(args: argparse.Namespace) -> dict[str, object]:
    capabilities: dict[str, object] | None = None
    try:
        capabilities = agent_capabilities(args.agent)
    except ValueError as exc:
        return {
            "status": "unsupported_contract",
            "agent": args.agent,
            "capabilities": capabilities,
            "error": str(exc),
        }
    if not capabilities["model_selection"]:
        return {
            "status": "unsupported_contract",
            "agent": args.agent,
            "capabilities": capabilities,
            "error": "The Agent runtime does not support selecting a model per request",
        }
    runtime_agent = backend_agent(args.agent)
    profile = resolve_model_profile(
        PROJECT_ROOT,
        profile_name=args.profile,
        model_override=args.model,
        agent=runtime_agent,
    )
    executable = args.agent_executable or default_agent_command(args.agent)
    detected = shutil.which(executable)
    if detected is None:
        return {
            "status": "not_installed", "agent": args.agent,
            "model": profile.model, "executable": executable,
        }
    runtime = find_multica_runtime(PROJECT_ROOT)
    env = os.environ.copy()
    if runtime_agent == "claude":
        env.pop("ANTHROPIC_API_KEY", None)
    env.update(profile.environment)
    env["AGENT_EVAL_AGENT_EXECUTABLE"] = detected
    with tempfile.TemporaryDirectory(prefix="agent-connectivity-") as temp:
        root = Path(temp)
        if runtime_agent == "claude":
            # Keep user-level Claude settings (especially env overrides and
            # apiKeyHelper) out of this gateway connectivity probe.
            env["CLAUDE_CONFIG_DIR"] = str(root / "claude-config")
        if runtime_agent == "codebuddy":
            codebuddy_config = root / "codebuddy-config"
            write_codebuddy_profile_config(codebuddy_config / "models.json", profile)
            env["CODEBUDDY_CONFIG_DIR"] = str(codebuddy_config)
        input_path, output_path = root / "input.json", root / "output.json"
        probe_id = f"connectivity-{uuid.uuid4().hex}"
        input_path.write_text(
            json.dumps({
                "messages": [{"role": "user", "content": f"Reply with exactly CONNECTIVITY_OK. Probe ID: {probe_id}"}],
                "workspace": str(root), "case_id": probe_id, "variant": "no-evaluation", "kwargs": {},
            }),
            encoding="utf-8",
        )
        if runtime_agent == "openclaw":
            config_path = root / "openclaw.json"
            write_openclaw_profile_config(config_path, profile)
            env["OPENCLAW_CONFIG_PATH"] = str(config_path)
        command = [
            str(runtime), "--input", str(input_path), "--output", str(output_path),
            "--agent", runtime_agent, "--model", profile.model_for_agent(runtime_agent),
            "--executable", detected, "--timeout-seconds", str(args.timeout), "--max-turns", "1",
        ]
        for value in profile.agent_args:
            command.extend(["--extra-arg", value])
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            process = subprocess.run(
                command, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
                timeout=args.timeout + 10, check=False,
            )
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "agent": args.agent, "model": profile.model}
        finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        result = json.loads(output_path.read_text(encoding="utf-8")) if output_path.is_file() else {}
    combined = "\n".join(
        str(value or "") for value in (
            process.stdout, process.stderr, result.get("stderr"), result.get("final_message")
        )
    )
    markers = ("invalid api key", "authentication error", "unauthorized", "model not found")
    marker = next((item for item in markers if item in combined.lower()), None)
    database_trace: dict[str, object] = {"status": "not_requested"}
    model_verification: dict[str, object] = {
        "status": "not_requested", "verified": None, "expected_model": profile.model
    }
    if args.database_verify and profile.api_base:
        try:
            rows: list[dict[str, object]] = []
            for attempt in range(6):
                rows = fetch_model_interactions(
                    PROJECT_ROOT,
                    started_at=started_at,
                    finished_at=finished_at,
                    model=profile.model,
                )
                if rows or attempt == 5:
                    break
                time.sleep(2)
            database_trace = summarize_model_interactions(rows, exact=False)
            model_verification = verify_requested_model(
                rows,
                expected_model=profile.model,
                accepted_model_groups=[profile.model_for_agent(runtime_agent).removeprefix("custom-local:")],
                exact=False,
            )
        except Exception as exc:
            database_trace = {"status": "unavailable", "error": str(exc)}
            model_verification = {
                "status": "unverified", "verified": False,
                "expected_model": profile.model, "reason": "database_trace_unavailable",
            }
    ok = (
        process.returncode == 0
        and result.get("exit_code") == 0
        and marker is None
        and (not args.database_verify or not profile.api_base or model_verification.get("verified") is True)
    )
    return {
        "status": "connected" if ok else "failed", "agent": args.agent,
        "model": profile.model, "agent_model": profile.model_for_agent(runtime_agent),
        "executable": detected, "runtime_exit_code": process.returncode,
        "agent_exit_code": result.get("exit_code"), "response": result.get("final_message"),
        "database_trace": database_trace, "model_verification": model_verification,
        "error": result.get("stderr") or (f"Detected error marker: {marker}" if marker else None),
    }


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
                    "capabilities": agent_capabilities(agent),
                    "evaluation_contract": describe_agent_contract(agent),
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
    if args.command == "check-agent":
        result = _check_agent(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "connected" else 1)
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
        require_model_verification=args.require_model_verification,
        user_id=args.user_id,
        task_name=args.task_name,
        task_id=args.task_id,
        client_task_id=args.client_task_id,
        run_llm_judge_enabled=args.llm_judge,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(
        0 if result["skill_up_exit_code"] == 0 and result.get("status", "completed") == "completed" else 1
    )


if __name__ == "__main__":
    main()
