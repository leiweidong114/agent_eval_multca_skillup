from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent_eval.runner import _retryable_infrastructure_message, run_evaluation
from agent_eval.codebuddy_proxy import CodeBuddyCompatibilityProxy
from agent_eval.cli_catalog import (
    SCHEMATIC_PIPELINE_SKILLS,
    compose_skill_bundle,
    list_results,
    list_skills,
)
from agent_eval.agent_contract import describe_agent_contract
from agent_eval.database import (
    database_health,
    fetch_model_interactions,
    wait_for_model_interactions,
    summarize_model_interactions,
    verify_requested_model,
)
from agent_eval.model_config import (
    describe_model_config,
    discover_available_models,
    gateway_request_headers,
    load_litellm_model_catalog,
    load_runtime_settings,
    refresh_litellm_model_catalog,
    resolve_config_secret,
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
from agent_eval.litellm_trace import create_trace_key, delete_trace_key
from agent_eval.failure import describe_evaluation_failure
from agent_eval.env_config import apply_root_env
from agent_eval.skill_sources import resolve_external_skill
from agent_eval.schematic_tasks import DEFAULT_SCHEMATIC_TASK_TYPE


# backend/src/agent_eval/cli.py -> parents[2] = backend
PROJECT_ROOT = Path(__file__).resolve().parents[2]
apply_root_env(PROJECT_ROOT)
DEFAULT_CONNECTIVITY_PROMPT = "Reply with exactly CONNECTIVITY_OK."
DEFAULT_SUBAGENT_PROMPT = (
    "必须启动一个真实 subagent，让它只回复 SUBAGENT_OK；等待它完成后，"
    "你只回复 PARENT_OK:SUBAGENT_OK。不得由主 Agent 模拟子代理结果。"
)


def _add_multi_eval_arguments(parser: argparse.ArgumentParser, *, pipeline: bool = False) -> None:
    if not pipeline:
        parser.add_argument("--skill", required=True)
    parser.add_argument("--agent", action="append", required=True)
    parser.add_argument("--profile", help=argparse.SUPPRESS)
    parser.add_argument(
        "--model",
        required=True,
        help="Provider model id; JustDo applies it as a session-only model override",
    )
    parser.add_argument(
        "--prompt",
        required=not pipeline,
        help=(
            "Ad-hoc evaluation prompt. For pipeline-eval, either --prompt or at least one "
            "--case must be supplied."
        ),
    )
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--must-contain", action="append", default=[])
    parser.add_argument("--must-not-contain", action="append", default=[])
    parser.add_argument("--workers", type=int, default=2, help="Concurrent Agent evaluations")
    parser.add_argument("--parallelism", type=int, default=1, help="Case concurrency per Agent")
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--output-dir")
    parser.add_argument("--user", "--user-id", dest="user_id", default="local")
    parser.add_argument("--task-name")
    parser.add_argument("--benchmark", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--database-trace", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--require-model-verification", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--llm-judge", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--evaluator", dest="evaluator_id", help="Installed evaluator id")


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
    run.add_argument(
        "--model",
        required=True,
        help="Provider model id; JustDo applies it as a session-only model override",
    )
    run.add_argument("--profile", help=argparse.SUPPRESS)
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
    run.add_argument("--evaluator", dest="evaluator_id", help="Installed evaluator id")

    commands.add_parser("doctor", help="Check the local skill-up and Multica runtime")
    gateway_check = commands.add_parser("check-litellm", help="Check API authentication and optional real inference")
    gateway_check.add_argument("--model", help="Also send HI to this model")
    gateway_check.add_argument("--timeout", type=float, default=30)
    commands.add_parser("check-database", help="Check PostgreSQL and LiteLLM SpendLogs access")
    agents = commands.add_parser("agents", help="List locally available evaluation Agents")
    agents.add_argument(
        "--all", action="store_true",
        help="Also show Agent backends whose executable is not installed",
    )
    models = commands.add_parser(
        "models", help="List the cached LiteLLM model catalog or refresh it from /v1/models"
    )
    models.add_argument(
        "--refresh", action="store_true",
        help="Query LiteLLM and update config/litellm-models.json",
    )
    models.add_argument("--prefix", help="Only show model ids beginning with this prefix")
    models.add_argument("--list", action="store_true", help="Fetch all visible models without paid inference probes")
    models.add_argument(
        "--agent",
        help="Label an Agent-specific upstream text probe; use check-agent for actual Agent/tool verification",
    )
    models.add_argument(
        "--timeout", type=float, default=15.0,
        help="Timeout in seconds for each real inference probe during --refresh",
    )
    models.add_argument(
        "--workers", type=int, default=8,
        help="Maximum concurrent model probes during --refresh",
    )
    models.add_argument(
        "--show-unavailable", action="store_true",
        help="Include failed model probes and their diagnostic reasons",
    )
    skills = commands.add_parser("skills", help="List Skills available for evaluation")
    skills.add_argument(
        "--pipeline", action="store_true", help="Only show the four schematic pipeline Skills"
    )
    results = commands.add_parser("results", help="List saved evaluation reports")
    results.add_argument("--limit", type=int, default=20)
    results.add_argument("--agent")
    results.add_argument("--skill")
    results.add_argument("--status")
    results.add_argument("--results-root")
    check = commands.add_parser(
        "check-agent", help="Test one Agent/model connection without running an evaluation"
    )
    check.add_argument("--agent", required=True)
    check.add_argument("--profile", help=argparse.SUPPRESS)
    check.add_argument("--model", help="LiteLLM model id; defaults to LITELLM_MODEL or gateway default")
    check.add_argument("--agent-executable")
    check.add_argument("--timeout", type=int, default=120)
    check.add_argument(
        "--max-turns",
        type=int,
        default=8,
        help="Maximum Agent turns; subagent probes normally need more than one turn",
    )
    check.add_argument(
        "--extra-arg",
        action="append",
        default=[],
        help="Extra Agent CLI argument; repeatable",
    )
    check.add_argument(
        "--prompt",
        default=DEFAULT_CONNECTIVITY_PROMPT,
        help="Minimal prompt sent by the connectivity probe",
    )
    check.add_argument(
        "--verify-subagent",
        action="store_true",
        help="Require a real child Agent result and database-confirmed use of the selected model",
    )
    check.add_argument(
        "--database-verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Confirm the requested model in PostgreSQL after the connectivity probe",
    )
    prompt = commands.add_parser(
        "prompt", help="Send the same prompt to one or more Agents concurrently"
    )
    prompt.add_argument("--agent", action="append", required=True)
    prompt.add_argument("--profile", help=argparse.SUPPRESS)
    prompt.add_argument("--model", required=True)
    prompt.add_argument("--prompt", required=True)
    prompt.add_argument("--workers", type=int, default=2)
    prompt.add_argument("--timeout", type=int, default=120)
    prompt.add_argument(
        "--database-verify", action=argparse.BooleanOptionalAction, default=True
    )
    multi = commands.add_parser(
        "run-multi", help="Evaluate one Skill with multiple Agents concurrently"
    )
    _add_multi_eval_arguments(multi)
    pipeline = commands.add_parser(
        "pipeline-eval", help="Evaluate the complete four-Skill schematic pipeline"
    )
    _add_multi_eval_arguments(pipeline, pipeline=True)
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
    verify_subagent = bool(getattr(args, "verify_subagent", False))
    if verify_subagent and not args.database_verify:
        return {
            "status": "failed",
            "agent": args.agent,
            "failure": {"category": "subagent_database_verification_required", "retryable": False},
            "error": "--verify-subagent requires --database-verify",
        }
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
    env.update(profile.environment)
    user_id = getattr(args, "user_id", "local")
    internal_headers = gateway_request_headers(PROJECT_ROOT, profile, user_id)
    env["AGENT_EVAL_AGENT_EXECUTABLE"] = detected
    env["AGENT_EVAL_REQUESTED_AGENT"] = args.agent.strip().lower()
    env["AGENT_EVAL_SUBAGENT_MODEL"] = profile.model
    trace_key = None
    if args.database_verify and profile.api_base:
        health = database_health(PROJECT_ROOT)
        if health.get("status") != "ok":
            return {
                "status": "failed", "agent": args.agent, "model": profile.model,
                "failure": {
                    "category": "postgresql_unavailable",
                    "retryable": _retryable_infrastructure_message(
                        str(health.get("error") or health.get("status"))
                    ),
                },
                "error": health.get("error") or health.get("status"),
            }
        try:
            trace_key = create_trace_key(
                profile.api_base,
                profile.gateway_model_for_agent(runtime_agent),
                f"connectivity-{uuid.uuid4().hex}",
                master_key=resolve_config_secret(PROJECT_ROOT, "LITELLM_MASTER_KEY"),
                request_headers=internal_headers,
                metadata={"agent_eval_user_id": user_id, "agent_eval_agent": args.agent},
            )
        except Exception as exc:
            return {
                "status": "failed", "agent": args.agent, "model": profile.model,
                "failure": {"category": "trace_key_unavailable", "retryable": False},
                "error": str(exc),
            }
        if trace_key is None:
            return {
                "status": "failed", "agent": args.agent, "model": profile.model,
                "failure": {"category": "trace_key_not_configured", "retryable": False},
                "error": "LITELLM_MASTER_KEY is required for exact model verification",
            }
        for key_name in (
            "LITELLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN", "MINIMAX_API_KEY",
        ):
            env[key_name] = trace_key.key
    protocol_probe: dict[str, object] = {"status": "not_required"}
    if profile.api_base and runtime_agent == "codex":
        endpoint = profile.api_base.rstrip("/") + "/chat/completions"
        try:
            response = httpx.post(
                endpoint,
                headers={"Authorization": f"Bearer {profile.environment['LITELLM_API_KEY']}"},
                json={"model": profile.model, "messages": [{"role": "user", "content": "HI"}], "stream": False},
                timeout=min(float(args.timeout), 45.0),
            )
            protocol_probe = {
                "status": "available" if response.is_success else "unavailable",
                "endpoint": "chat/completions (local Responses adapter)",
                "status_code": response.status_code,
            }
            if not response.is_success:
                failure = describe_evaluation_failure(
                    response.text,
                    returncode=1,
                    status_code=response.status_code,
                    component="agent_protocol_probe",
                )
                protocol_probe["failure"] = failure
                cleanup: dict[str, object] = {"status": "not_created"}
                if trace_key is not None:
                    try:
                        delete_trace_key(trace_key)
                        cleanup = {"status": "deleted", "alias": trace_key.alias}
                    except Exception as exc:
                        cleanup = {
                            "status": "delete_failed", "alias": trace_key.alias,
                            "error": str(exc),
                        }
                return {
                    "status": "failed",
                    "agent": args.agent,
                    "model": profile.model,
                    "agent_model": profile.model_for_agent(runtime_agent),
                    "executable": detected,
                    "protocol_probe": protocol_probe,
                    "trace_key_alias": trace_key.alias if trace_key else None,
                    "trace_key_cleanup": cleanup,
                    "failure": failure,
                    "error": failure.get("technical_detail") if failure else response.text,
                }
        except (httpx.HTTPError, ValueError) as exc:
            failure = describe_evaluation_failure(
                str(exc), returncode=1, component="agent_protocol_probe"
            )
            protocol_probe = {
                "status": "unavailable", "endpoint": "responses", "failure": failure
            }
            if trace_key is not None:
                try:
                    delete_trace_key(trace_key)
                except Exception:
                    pass
            return {
                "status": "failed", "agent": args.agent, "model": profile.model,
                "agent_model": profile.model_for_agent(runtime_agent),
                "executable": detected, "protocol_probe": protocol_probe,
                "failure": failure, "error": str(exc),
            }
    # Some Windows Agent CLIs keep their workspace directory handle open for
    # a short time after exit. Do not let best-effort temp cleanup replace the
    # real connectivity result with a recursive PermissionError traceback.
    with tempfile.TemporaryDirectory(
        prefix="agent-connectivity-", ignore_cleanup_errors=True
    ) as temp:
        root = Path(temp)
        if profile.api_base and runtime_agent == "claude":
            # Keep user-level Claude settings (especially env overrides and
            # apiKeyHelper) out of this gateway connectivity probe.
            env["CLAUDE_CONFIG_DIR"] = str(root / "claude-config")
        if profile.api_base and runtime_agent == "codebuddy":
            codebuddy_config = root / "codebuddy-config"
            write_codebuddy_profile_config(codebuddy_config / "models.json", profile)
            env["CODEBUDDY_CONFIG_DIR"] = str(codebuddy_config)
        input_path, output_path = root / "input.json", root / "output.json"
        probe_id = f"connectivity-{uuid.uuid4().hex}"
        probe_prompt = args.prompt
        if verify_subagent and probe_prompt == DEFAULT_CONNECTIVITY_PROMPT:
            probe_prompt = DEFAULT_SUBAGENT_PROMPT
        input_path.write_text(
            json.dumps({
                "messages": [{"role": "user", "content": probe_prompt}],
                "workspace": str(root), "case_id": probe_id, "variant": "no-evaluation", "kwargs": {},
            }),
            encoding="utf-8",
        )
        if profile.api_base and runtime_agent == "openclaw":
            config_path = root / "openclaw.json"
            write_openclaw_profile_config(config_path, profile)
            env["OPENCLAW_CONFIG_PATH"] = str(config_path)
            state_path = root / "openclaw-state"
            state_path.mkdir()
            env["OPENCLAW_STATE_DIR"] = str(state_path)
        command = [
            str(runtime), "--input", str(input_path), "--output", str(output_path),
            "--agent", runtime_agent, "--model", profile.model_for_agent(runtime_agent),
            "--executable", detected, "--timeout-seconds", str(args.timeout),
            "--max-turns", str(getattr(args, "max_turns", 8)),
        ]
        for value in [*profile.agent_args, *getattr(args, "extra_arg", [])]:
            command.extend(["--extra-arg", value])
        resilience_proxy = None
        if profile.api_base and runtime_agent in {"claude", "codex", "codebuddy", "openclaw"}:
            resilience_proxy = CodeBuddyCompatibilityProxy(
                profile.api_base,
                timeout=args.timeout,
                forced_model=profile.gateway_model_for_agent(runtime_agent),
            strip_tools_after_result=False,
                translate_protocols=runtime_agent in {"claude", "codex"},
            )
            resilience_proxy.start()
            env["OPENAI_BASE_URL"] = resilience_proxy.openai_base_url
            env["MINIMAX_BASE_URL"] = resilience_proxy.openai_base_url
            env["ANTHROPIC_BASE_URL"] = resilience_proxy.anthropic_base_url
            env["AGENT_EVAL_PROVIDER_BASE_URL"] = resilience_proxy.openai_base_url
            if env.get("OPENCODE_CONFIG_CONTENT"):
                opencode_config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
                opencode_config["provider"]["litellm"]["options"]["baseURL"] = resilience_proxy.openai_base_url
                env["OPENCODE_CONFIG_CONTENT"] = json.dumps(opencode_config, ensure_ascii=False)
            if runtime_agent == "claude":
                env["ANTHROPIC_BASE_URL"] = resilience_proxy.anthropic_base_url
            elif runtime_agent == "codex":
                command.extend(["--extra-arg", "-c", "--extra-arg",
                                f'model_providers.litellm.base_url="{resilience_proxy.openai_base_url}"'])
            elif runtime_agent == "codebuddy":
                write_codebuddy_profile_config(
                    Path(env["CODEBUDDY_CONFIG_DIR"]) / "models.json",
                    profile,
                    endpoint=resilience_proxy.url,
                )
            else:
                if runtime_agent == "openclaw":
                    write_openclaw_profile_config(
                        Path(env["OPENCLAW_CONFIG_PATH"]), profile, workspace=root,
                        api_base_override=resilience_proxy.openai_base_url,
                    )
                elif runtime_agent == "codex":
                    command = [
                        f'model_providers.litellm.base_url="{resilience_proxy.openai_base_url}"'
                        if item.startswith("model_providers.litellm.base_url=") else item
                        for item in command
                    ]
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            try:
                process = subprocess.run(
                    command, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
                    timeout=args.timeout + 10, check=False,
                )
            except subprocess.TimeoutExpired:
                try:
                    delete_trace_key(trace_key)
                except Exception:
                    pass
                return {"status": "timeout", "agent": args.agent, "model": profile.model}
        finally:
            if resilience_proxy is not None:
                resilience_proxy.close()
        finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        result = json.loads(output_path.read_text(encoding="utf-8")) if output_path.is_file() else {}
    combined = "\n".join(
        str(value or "") for value in (
            process.stdout, process.stderr, result.get("stderr"), result.get("final_message")
        )
    )
    markers = ("invalid api key", "authentication error", "unauthorized", "model not found")
    marker = next((item for item in markers if item in combined.lower()), None)
    rows: list[dict[str, object]] = []
    database_trace: dict[str, object] = {"status": "not_requested"}
    model_verification: dict[str, object] = {
        "status": "not_requested", "verified": None, "expected_model": profile.model
    }
    if args.database_verify and profile.api_base:
        try:
            rows = wait_for_model_interactions(
                    PROJECT_ROOT,
                    started_at=started_at,
                    finished_at=finished_at,
                    model=profile.model,
                    key_alias=trace_key.alias if trace_key else None,
                    task_id=probe_id,
                    user_id=user_id,
                    agent=args.agent,
                    requested_model=profile.model,
                )
            database_trace = summarize_model_interactions(rows, exact=trace_key is not None)
            model_verification = verify_requested_model(
                rows,
                expected_model=profile.model,
                accepted_model_groups=[
                    profile.gateway_model_for_agent(runtime_agent),
                ],
                exact=trace_key is not None,
            )
        except Exception as exc:
            database_trace = {"status": "unavailable", "error": str(exc)}
            model_verification = {
                "status": "unverified", "verified": False,
                "expected_model": profile.model, "reason": "database_trace_unavailable",
            }
    trace_key_alias = trace_key.alias if trace_key else None
    trace_key_cleanup: dict[str, object] = {"status": "not_created"}
    if trace_key is not None:
        try:
            delete_trace_key(trace_key)
            trace_key_cleanup = {"status": "deleted", "alias": trace_key.alias}
        except Exception as exc:
            trace_key_cleanup = {
                "status": "delete_failed", "alias": trace_key.alias,
                "error": str(exc),
            }
    subagent_verification = _verify_subagent_evidence(
        args.agent, profile.model, result, rows
    ) if verify_subagent else {"status": "not_requested", "verified": None}
    ok = (
        process.returncode == 0
        and result.get("exit_code") == 0
        and bool(str(result.get("final_message") or "").strip())
        and marker is None
        and (not args.database_verify or not profile.api_base or model_verification.get("verified") is True)
        and (trace_key is None or trace_key_cleanup["status"] == "deleted")
        and (not verify_subagent or subagent_verification["verified"] is True)
    )
    error_text = result.get("stderr") or (f"Detected error marker: {marker}" if marker else None)
    failure = None if ok else describe_evaluation_failure(
        combined or str(error_text or "Agent connectivity check failed"),
        returncode=int(result.get("exit_code") or process.returncode or 1),
        component="agent_model_connectivity",
    )
    if not ok and process.returncode == 0 and result.get("exit_code") == 0 and not marker:
        if verify_subagent and subagent_verification.get("verified") is not True:
            failure = {
                "category": "subagent_verification_failed", "retryable": True,
                "summary": "Agent 已返回，但未找到真实子 Agent 的成功证据",
                "detail": subagent_verification.get("reason"),
                "suggested_action": "检查 Subagent 工具结果和同一 trace key 下的子模型调用后重试。",
            }
        elif args.database_verify and model_verification.get("verified") is not True:
            failure = {"category": "model_verification_failed", "retryable": True,
                       "summary": "Agent 已返回，但数据库尚未确认指定模型调用",
                       "detail": model_verification.get("reason"),
                       "suggested_action": "检查数据库日志延迟及模型映射后重试；不能把未核验结果当作成功。"}
        elif trace_key_cleanup.get("status") == "delete_failed":
            failure = {"category": "trace_key_cleanup_failed", "retryable": True,
                       "summary": "模型已调用，但本次临时 trace key 清理失败"}
    return {
        "status": "connected" if ok else "failed", "agent": args.agent,
        "model": profile.model, "agent_model": profile.model_for_agent(runtime_agent),
        "executable": detected, "runtime_exit_code": process.returncode,
        "agent_exit_code": result.get("exit_code"), "response": result.get("final_message"),
        "database_trace": database_trace, "model_verification": model_verification,
        "subagent_verification": subagent_verification,
        "protocol_probe": protocol_probe,
        "trace_key_alias": trace_key_alias, "trace_key_cleanup": trace_key_cleanup,
        "failure": failure,
        "error": error_text,
    }


def _verify_subagent_evidence(
    agent: str,
    model: str,
    result: dict[str, object],
    rows: list[dict[str, object]],
) -> dict[str, object]:
    """Require tool-result evidence, not a parent-authored marker."""
    tool_calls: list[tuple[str, str, str]] = []
    tool_results: list[tuple[str, str]] = []
    assistant_results: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            role = str(value.get("role") or "").lower()
            if role == "tool" and value.get("content") is not None:
                tool_results.append((
                    str(value.get("tool_call_id") or value.get("call_id") or ""),
                    str(value.get("content")),
                ))
            if role == "assistant" and value.get("content") is not None:
                assistant_results.append(str(value.get("content")))
            direct = value.get("tool_call")
            if isinstance(direct, dict):
                tool_calls.append((
                    str(direct.get("id") or direct.get("call_id") or ""),
                    str(direct.get("name") or ""),
                    json.dumps(direct.get("arguments") or {}, ensure_ascii=False, default=str),
                ))
            calls = value.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function") or {}
                    if isinstance(function, dict):
                        tool_calls.append((
                            str(call.get("id") or call.get("call_id") or ""),
                            str(function.get("name") or call.get("name") or ""),
                            str(function.get("arguments") or call.get("arguments") or ""),
                        ))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(result.get("transcript") or [])
    for row in rows:
        walk(row.get("proxy_server_request") or {})
        walk(row.get("response") or {})

    normalized_agent = agent.strip().lower()
    native_names = {
        "codex": {"spawn_agent", "collaboration__spawn_agent"},
        "claude": {"agent"},
        "codebuddy": {"agent"},
        "opencode": {"task"},
        "justdo": {"sessions_spawn"},
    }
    expected = native_names.get(normalized_agent, set())
    invocation = False
    native_invocation = False
    for _call_id, name, arguments in tool_calls:
        lowered = name.lower()
        if lowered in expected:
            invocation = True
            native_invocation = True
        if normalized_agent == "codex" and lowered.endswith("__spawn_agent"):
            invocation = True
            native_invocation = True
        if normalized_agent == "openclaw" and lowered == "exec" and "openclaw agent exec" in arguments:
            invocation = True
        if normalized_agent == "justdo" and lowered == "exec" and "agent-eval check-agent --agent justdo" in arguments:
            invocation = True

    result_text = "\n".join(content for _call_id, content in tool_results)
    call_by_id = {
        call_id: (name.lower(), arguments)
        for call_id, name, arguments in tool_calls
        if call_id
    }
    correlated_child_result = False
    for call_id, content in tool_results:
        call = call_by_id.get(call_id)
        if not call or "SUBAGENT_OK" not in content:
            continue
        name, arguments = call
        if normalized_agent == "codex" and name.endswith("__wait_agent"):
            correlated_child_result = True
        elif normalized_agent in {"claude", "codebuddy"} and name == "agent":
            correlated_child_result = True
        elif normalized_agent == "opencode" and name == "task":
            correlated_child_result = True
        elif normalized_agent == "openclaw" and name == "exec" and "openclaw agent exec" in arguments:
            correlated_child_result = True
        elif normalized_agent == "justdo" and (
            name == "sessions_spawn"
            or (name == "exec" and "agent-eval check-agent --agent justdo" in arguments)
        ):
            correlated_child_result = True
        elif normalized_agent == "justdo" and name == "sessions_yield" and native_invocation:
            # Current JustDo/OpenClaw returns the completed child payload from
            # ``sessions_yield`` rather than from ``sessions_spawn``.  Accept
            # only a structured, successful subagent result so an unrelated
            # tool or parent-authored marker cannot satisfy verification.
            try:
                yielded = json.loads(content)
            except (TypeError, ValueError):
                yielded = None
            if isinstance(yielded, dict) and yielded.get("status") == "completed":
                results = yielded.get("results")
                if isinstance(results, list):
                    correlated_child_result = any(
                        isinstance(item, dict)
                        and item.get("status") == "ok"
                        and str(item.get("result") or "").strip() == "SUBAGENT_OK"
                        and ":subagent:" in str(item.get("sessionKey") or "")
                        for item in results
                    )
    child_model_marker = any(
        text.strip() == "SUBAGENT_OK" for text in assistant_results
    )
    if normalized_agent == "openclaw":
        child_result = correlated_child_result or (
            child_model_marker
            or (
                "SUBAGENT_OK" in result_text
                and ('"ok": true' in result_text or "'ok': True" in result_text)
                and model in result_text
            )
        )
    elif normalized_agent == "justdo":
        child_result = correlated_child_result or (
            child_model_marker
            or (
                "SUBAGENT_OK" in result_text
                and ('"status": "connected"' in result_text or "'status': 'connected'" in result_text)
                and model in result_text
            )
        )
    else:
        child_result = correlated_child_result or child_model_marker

    # Some CLIs retain a short progress preface in FinalMessage even though
    # the terminal answer is the requested marker. Require the exact marker at
    # the end; tool-result evidence above still prevents parent simulation.
    final_marker = str(result.get("final_message") or "").strip().endswith(
        "PARENT_OK:SUBAGENT_OK"
    )
    same_model_calls = sum(
        1 for row in rows
        if row.get("status") == "success"
        and model in {str(row.get("model") or ""), str(row.get("model_group") or "")}
    )
    verified = invocation and child_result and final_marker and same_model_calls >= 2
    missing = []
    if not invocation:
        missing.append("subagent_invocation")
    if not child_result:
        missing.append("successful_child_tool_result")
    if not final_marker:
        missing.append("parent_completion_marker")
    if same_model_calls < 2:
        missing.append("parent_and_child_model_calls")
    return {
        "status": "verified" if verified else "unverified",
        "verified": verified,
        "transport": "native" if native_invocation else "isolated_child_process",
        "tool_calls": sorted({name for _, name, _ in tool_calls if name}),
        "successful_matching_model_calls": same_model_calls,
        "reason": None if verified else "missing: " + ", ".join(missing),
    }


def _unique_agents(values: list[str], workers: int) -> list[str]:
    agents = list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
    if not agents:
        raise ValueError("Select at least one --agent")
    if workers < 1:
        raise ValueError("--workers must be greater than zero")
    return agents


def _probe_local_agent(executable: str, *, timeout: float = 20.0) -> dict[str, object]:
    """Check that a discovered CLI can actually start, not merely exist on PATH."""
    try:
        process = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        output = (process.stdout or process.stderr or "").strip().splitlines()
        return {
            "available": process.returncode == 0,
            "version": output[0][:300] if output else None,
            "exit_code": process.returncode,
            "error": None if process.returncode == 0 else (output[0][:500] if output else None),
        }
    except subprocess.TimeoutExpired:
        return {"available": False, "version": None, "exit_code": None, "error": "version probe timed out"}
    except OSError as exc:
        return {"available": False, "version": None, "exit_code": None, "error": str(exc)}


def _prompt_batch(args: argparse.Namespace) -> dict[str, object]:
    agents = _unique_agents(args.agent, args.workers)
    rows: list[dict[str, object]] = []

    def run_one(agent: str) -> dict[str, object]:
        return _check_agent(
            argparse.Namespace(
                agent=agent,
                profile=args.profile,
                model=args.model,
                agent_executable=None,
                timeout=args.timeout,
                database_verify=args.database_verify,
                prompt=args.prompt,
            )
        )

    with ThreadPoolExecutor(max_workers=min(args.workers, len(agents))) as pool:
        futures = {pool.submit(run_one, agent): agent for agent in agents}
        for future in as_completed(futures):
            agent = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append({"status": "exception", "agent": agent, "error": str(exc)})
    rows.sort(key=lambda item: agents.index(str(item.get("agent"))))
    passed = sum(item.get("status") == "connected" for item in rows)
    return {
        "status": "completed" if passed == len(rows) else ("partial_failed" if passed else "failed"),
        "prompt": args.prompt,
        "model": args.model,
        "workers": min(args.workers, len(agents)),
        "passed": passed,
        "total": len(rows),
        "results": rows,
    }


def _resolve_cli_skill(value: str) -> Path:
    direct = Path(value).resolve()
    if (direct / "SKILL.md").is_file():
        return direct
    candidate = (PROJECT_ROOT / "skills" / value).resolve()
    if (candidate / "SKILL.md").is_file():
        return candidate
    external = resolve_external_skill(PROJECT_ROOT, value)
    if external is not None:
        return external
    raise FileNotFoundError(f"Skill was not found: {value}")


def _evaluation_batch(
    args: argparse.Namespace,
    *,
    skill_dir: Path,
    selected_skills: list[str],
    evaluation_type: str,
    schematic_task_type: str | None = None,
) -> dict[str, object]:
    agents = _unique_agents(args.agent, args.workers)
    rows: list[dict[str, object]] = []

    def evaluation_passed(result: dict[str, object]) -> bool:
        if result.get("status", "completed") != "completed":
            return False
        cases = [
            case
            for iteration in (result.get("results") or [])
            for case in (iteration.get("case_results") or [])
        ]
        return all(case.get("status") == "PASS" for case in cases) if cases else True

    def run_one(agent: str) -> dict[str, object]:
        task_id = uuid.uuid4().hex
        try:
            result = run_evaluation(
                project_root=PROJECT_ROOT,
                skill_dir=str(skill_dir),
                agent=agent,
                model=args.model,
                profile=args.profile,
                case_files=args.case,
                prompt=args.prompt,
                must_contain=args.must_contain,
                must_not_contain=args.must_not_contain,
                parallelism=args.parallelism,
                iterations=args.iterations,
                timeout_seconds=args.timeout,
                max_turns=args.max_turns,
                benchmark=args.benchmark,
                output_dir=args.output_dir,
                collect_database_trace=args.database_trace,
                require_model_verification=args.require_model_verification,
                task_id=task_id,
                user_id=args.user_id,
                task_name=args.task_name or skill_dir.name,
                run_llm_judge_enabled=args.llm_judge,
                evaluation_type=evaluation_type,
                selected_skills=selected_skills,
                evaluator_id=getattr(args, "evaluator_id", None),
                schematic_task_type=schematic_task_type,
            )
            scores = result.get("scores") or {}
            passed = evaluation_passed(result)
            return {
                "agent": agent,
                "status": result.get("status", "completed"),
                "evaluation_passed": passed,
                "task_id": result.get("task_id") or task_id,
                "model": result.get("provider_model") or result.get("model"),
                "overall_score": scores.get("overall_score"),
                "result_score": scores.get("result_dimension_score"),
                "process_score": scores.get("process_dimension_score"),
                "skill_quality_score": scores.get("skill_quality_dimension_score"),
                "result_dir": result.get("result_dir"),
                "failure": result.get("failure"),
            }
        except Exception as exc:
            return {
                "agent": agent,
                "status": "exception",
                "task_id": task_id,
                "model": args.model,
                "error": f"{type(exc).__name__}: {exc}",
            }

    with ThreadPoolExecutor(max_workers=min(args.workers, len(agents))) as pool:
        futures = {pool.submit(run_one, agent): agent for agent in agents}
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda item: agents.index(str(item.get("agent"))))
    passed = sum(item.get("evaluation_passed") is True for item in rows)
    return {
        "status": "completed" if passed == len(rows) else ("partial_failed" if passed else "failed"),
        "evaluation_type": evaluation_type,
        "skills": selected_skills,
        "prompt": args.prompt,
        "model": args.model,
        "workers": min(args.workers, len(agents)),
        "passed": passed,
        "total": len(rows),
        "results": rows,
    }


def main() -> None:
    apply_root_env(PROJECT_ROOT)
    args = _parser().parse_args()
    if args.command in {"check-litellm", "check-database"}:
        from agent_eval.diagnostics import check_database, check_litellm
        result = (check_database(PROJECT_ROOT) if args.command == "check-database"
                  else check_litellm(PROJECT_ROOT, model=args.model, timeout=args.timeout))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "ok" else 1)
    if args.command == "agents":
        result = []
        for agent in SUPPORTED_AGENTS:
            command = default_agent_command(agent)
            detected = shutil.which(command)
            if detected is None and not args.all:
                continue
            availability = (
                _probe_local_agent(detected)
                if detected is not None
                else {"available": False, "version": None, "exit_code": None, "error": "not found"}
            )
            if not availability["available"] and not args.all:
                continue
            result.append(
                {
                    "agent": agent,
                    "default_command": command,
                    "detected_executable": detected,
                    "availability": availability,
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
    if args.command == "models":
        if args.list:
            result = discover_available_models(PROJECT_ROOT)
            result["connectivity_tested"] = False
            if args.prefix:
                result["models"] = [m for m in result["models"] if m["id"].startswith(args.prefix)]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(0 if result["litellm_available"] else 1)
        if args.agent and not args.refresh:
            print(json.dumps({
                "status": "failed",
                "error": "--agent requires --refresh because protocol availability is live",
            }, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        try:
            result = (
                refresh_litellm_model_catalog(
                    PROJECT_ROOT,
                    probe_timeout=args.timeout,
                    probe_workers=args.workers,
                    probe_agent=backend_agent(args.agent) if args.agent else None,
                )
                if args.refresh
                else load_litellm_model_catalog(PROJECT_ROOT)
            )
        except (FileNotFoundError, ValueError) as exc:
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        if args.prefix:
            result = dict(result)
            result["models"] = [
                item for item in result["models"]
                if str(item.get("id") or "").startswith(args.prefix)
            ]
            result["filtered_model_count"] = len(result["models"])
        if not args.show_unavailable and "unavailable_models" in result:
            result = dict(result)
            result.pop("unavailable_models", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result.get("status") != "failed" else 1)
    if args.command == "skills":
        print(
            json.dumps(
                list_skills(PROJECT_ROOT / "skills", pipeline_only=args.pipeline),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "results":
        root = Path(args.results_root).resolve() if args.results_root else PROJECT_ROOT / "evaluation_results"
        print(
            json.dumps(
                list_results(
                    root, limit=args.limit, agent=args.agent, skill=args.skill, status=args.status
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "check-agent":
        result = _check_agent(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "connected" else 1)
    if args.command == "prompt":
        result = _prompt_batch(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "completed" else 1)
    if args.command in {"run-multi", "pipeline-eval"}:
        if args.command == "pipeline-eval":
            runtime_settings = load_runtime_settings(PROJECT_ROOT)
            task_profile = (runtime_settings.get("schematic_task_profiles") or {}).get(
                DEFAULT_SCHEMATIC_TASK_TYPE, {}
            )
            configured = task_profile.get("skills") or runtime_settings.get("schematic_skills")
            selected_skills = (
                list(configured)
                if isinstance(configured, list) and configured
                else list(SCHEMATIC_PIPELINE_SKILLS)
            )
            skill_dir = compose_skill_bundle(PROJECT_ROOT, tuple(selected_skills))
            evaluation_type = "schematic"
            schematic_task_type = DEFAULT_SCHEMATIC_TASK_TYPE
            if not args.evaluator_id:
                args.evaluator_id = task_profile.get("evaluator_id")
        else:
            skill_dir = _resolve_cli_skill(args.skill)
            selected_skills = [skill_dir.name]
            evaluation_type = "skill"
            schematic_task_type = None
        result = _evaluation_batch(
            args,
            skill_dir=skill_dir,
            selected_skills=selected_skills,
            evaluation_type=evaluation_type,
            schematic_task_type=schematic_task_type,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "completed" else 1)
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
        evaluator_id=args.evaluator_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(
        0 if result["skill_up_exit_code"] == 0 and result.get("status", "completed") == "completed" else 1
    )


if __name__ == "__main__":
    main()
