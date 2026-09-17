"""Run the strict smart street-light four-Skill evaluation."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT / "src"))
sys.path.insert(0, str(BACKEND_ROOT))

from agent_eval.cli_catalog import SCHEMATIC_PIPELINE_SKILLS, compose_skill_bundle  # noqa: E402
from agent_eval.database import _sanitize  # noqa: E402
from agent_eval.runtime import SUPPORTED_AGENTS, default_agent_command  # noqa: E402
from agent_eval.runner import run_evaluation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", action="append")
    parser.add_argument("--all-agents", action="store_true")
    parser.add_argument("--model", default="glm-4.5-air")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args()

    agents = args.agent or ([
        agent for agent in SUPPORTED_AGENTS if shutil.which(default_agent_command(agent, BACKEND_ROOT))
    ] if args.all_agents else ["codex"])
    if not agents or args.workers < 1:
        parser.error("No installed agents or invalid worker count")
    output = args.output or (
        REPOSITORY_ROOT / ".runtime" / "verification"
        / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-schematic")
    )
    output.mkdir(parents=True, exist_ok=True)

    with urllib.request.urlopen("http://127.0.0.1:8631/api/health", timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"schematic service returned HTTP {response.status}")

    case = (
        BACKEND_ROOT
        / "skills"
        / "schematic-pipeline"
        / "evals"
        / "cases"
        / "smart-street-light-e2e.yaml"
    )
    bundle = compose_skill_bundle(BACKEND_ROOT)

    def run_agent(agent: str) -> dict[str, object]:
        started = time.monotonic()
        print(f"[{agent}] started", flush=True)
        try:
            result = run_evaluation(
                project_root=BACKEND_ROOT,
                skill_dir=str(bundle),
                agent=agent,
                model=args.model,
                case_files=[str(case)],
                prompt=None,
                parallelism=1,
                iterations=1,
                timeout_seconds=args.timeout,
                max_turns=args.max_turns,
                benchmark=args.benchmark,
                output_dir=str(BACKEND_ROOT / "evaluation_results"),
                validate_only=args.validate_only,
                collect_database_trace=True,
                require_model_verification=True,
                user_id="local",
                task_name="智能路灯完整原理图评测",
                run_llm_judge_enabled=not args.no_judge,
                evaluation_type="schematic",
                selected_skills=list(SCHEMATIC_PIPELINE_SKILLS),
                evaluator_id="schematic-default",
                schematic_task_type="block_to_schematic",
                progress_callback=lambda stage, percent, message: print(
                    f"[{agent}] {percent:03d}% {stage}: {message}", flush=True
                ),
            )
        except Exception as exc:
            result = {"status": "exception", "error": f"{type(exc).__name__}: {exc}"}
        cases = [
            case_result
            for iteration in result.get("results", [])
            for case_result in iteration.get("case_results", [])
        ]
        acceptance = (((result.get("scoring") or {}).get("extensions") or {}).get("schematic") or {}).get("acceptance") or {}
        result["matrix_passed"] = bool(
            (args.validate_only and result.get("validated"))
            or (
                result.get("status", "completed") == "completed"
                and cases
                and all(item.get("status") == "PASS" for item in cases)
                and acceptance.get("accepted") is True
                and result.get("model_verification", {}).get("verified") is True
                and (args.no_judge or result.get("scoring", {}).get("llm_judge", {}).get("status") == "completed")
            )
        )
        result["duration_seconds"] = round(time.monotonic() - started, 2)
        safe = _sanitize(result, max_chars=None)
        (output / f"{agent}.json").write_text(
            json.dumps(safe, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"[{agent}] {result.get('status')} passed={result['matrix_passed']}", flush=True)
        return safe

    rows = []
    with ThreadPoolExecutor(max_workers=min(args.workers, len(agents))) as pool:
        futures = [pool.submit(run_agent, agent) for agent in agents]
        for future in as_completed(futures):
            rows.append(future.result())
    summary = {
        "model": args.model,
        "passed": sum(bool(row.get("matrix_passed")) for row in rows),
        "total": len(rows),
        "agents": rows,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"{summary['passed']}/{summary['total']}; evidence: {output}", flush=True)
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
