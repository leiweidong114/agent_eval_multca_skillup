"""Paid live Skill/Agent acceptance matrix with resumable evidence files.

Smoke mode covers every selected Skill and every installed Agent at least once.
Full mode runs the complete Skill x Agent cross product. Existing per-cell JSON
files are reused with --resume.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path[:0] = [str(BACKEND), str(BACKEND / "src")]

from agent_eval.database import _sanitize  # noqa: E402
from agent_eval.runtime import SUPPORTED_AGENTS, default_agent_command  # noqa: E402
from agent_eval.runner import run_evaluation  # noqa: E402


PIPELINE_SKILL = "schematic-pipeline"


def available_agents(requested: list[str] | None) -> list[str]:
    values = requested or list(SUPPORTED_AGENTS)
    return [
        value for value in values
        if shutil.which(default_agent_command(value, BACKEND))
    ]


def available_skills(requested: list[str] | None) -> list[str]:
    if requested:
        return requested
    return sorted(
        item.name for item in (BACKEND / "skills").iterdir()
        if item.is_dir() and item.name != PIPELINE_SKILL and (item / "SKILL.md").is_file()
    )


def matrix(skills: list[str], agents: list[str], full: bool) -> list[tuple[str, str]]:
    if full:
        return [(skill, agent) for skill in skills for agent in agents]
    # Deterministic rotation: all Skills run once and all Agents receive work.
    rows = [(skill, agents[index % len(agents)]) for index, skill in enumerate(skills)]
    covered = {agent for _, agent in rows}
    fallback_skill = "example-marker" if "example-marker" in skills else skills[0]
    rows.extend((fallback_skill, agent) for agent in agents if agent not in covered)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--agent", action="append")
    parser.add_argument("--skill", action="append")
    parser.add_argument("--full-cross-product", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    agents = available_agents(args.agent)
    skills = available_skills(args.skill)
    if not agents or not skills or args.workers < 1:
        parser.error("No installed Agents, no Skills, or invalid workers")
    output = args.output or ROOT / ".runtime" / "verification" / (
        datetime.now().strftime("%Y%m%d-%H%M%S") + "-skill-agent-matrix"
    )
    output.mkdir(parents=True, exist_ok=True)
    cells = matrix(skills, agents, args.full_cross_product)

    def run_cell(skill: str, agent: str) -> dict[str, object]:
        destination = output / f"{skill}__{agent}.json"
        if args.resume and destination.is_file():
            return json.loads(destination.read_text(encoding="utf-8"))
        skill_root = BACKEND / "skills" / skill
        cases = sorted((skill_root / "evals" / "cases").glob("*.yaml"))
        started = time.monotonic()
        print(f"[{skill}/{agent}] started", flush=True)
        try:
            result = run_evaluation(
                project_root=BACKEND,
                skill_dir=str(skill_root),
                agent=agent,
                model=args.model,
                case_files=[str(item) for item in cases],
                prompt=None,
                iterations=1,
                parallelism=1,
                timeout_seconds=args.timeout,
                max_turns=args.max_turns,
                benchmark=args.benchmark,
                collect_database_trace=True,
                require_model_verification=True,
                run_llm_judge_enabled=not args.no_judge,
                evaluator_id="skill-default",
                evaluation_type="skill",
                selected_skills=[skill],
                task_name=f"Skill兼容性测试-{skill}-{agent}",
                progress_callback=lambda stage, percent, message: print(
                    f"[{skill}/{agent}] {percent:03d}% {stage}: {message}", flush=True
                ),
            )
        except Exception as exc:
            result = {"status": "exception", "error": f"{type(exc).__name__}: {exc}"}
        acceptance = (((result.get("scoring") or {}).get("extensions") or {}).get("skill") or {}).get("acceptance") or {}
        result["matrix_passed"] = bool(
            result.get("status") == "completed"
            and acceptance.get("accepted") is True
            and result.get("model_verification", {}).get("verified") is True
            and (args.no_judge or result.get("scoring", {}).get("llm_judge", {}).get("status") == "completed")
        )
        result.update(matrix_skill=skill, matrix_agent=agent, duration_seconds=round(time.monotonic() - started, 2))
        safe = _sanitize(result, max_chars=None)
        destination.write_text(json.dumps(safe, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"[{skill}/{agent}] {result.get('status')} passed={result['matrix_passed']}", flush=True)
        return safe

    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=min(args.workers, len(cells))) as pool:
        futures = [pool.submit(run_cell, skill, agent) for skill, agent in cells]
        for future in as_completed(futures):
            rows.append(future.result())
    summary = {
        "mode": "full_cross_product" if args.full_cross_product else "coverage_smoke",
        "model": args.model,
        "passed": sum(bool(row.get("matrix_passed")) for row in rows),
        "total": len(rows),
        "skills": skills,
        "agents": agents,
        "runs": rows,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"{summary['passed']}/{summary['total']}; evidence: {output}", flush=True)
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
