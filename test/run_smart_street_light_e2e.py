"""Run the strict smart street-light four-Skill evaluation."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT / "src"))
sys.path.insert(0, str(BACKEND_ROOT))

from agent_eval.cli_catalog import SCHEMATIC_PIPELINE_SKILLS, compose_skill_bundle  # noqa: E402
from agent_eval.runner import run_evaluation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", default="codex")
    parser.add_argument("--model", default="glm-4.5-air")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()

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
    result = run_evaluation(
        project_root=BACKEND_ROOT,
        skill_dir=str(compose_skill_bundle(BACKEND_ROOT)),
        agent=args.agent,
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
        run_llm_judge_enabled=True,
        evaluation_type="schematic",
        selected_skills=list(SCHEMATIC_PIPELINE_SKILLS),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    cases = [
        case_result
        for iteration in result.get("results", [])
        for case_result in iteration.get("case_results", [])
    ]
    passed = result.get("status", "completed") == "completed" and all(
        item.get("status") == "PASS" for item in cases
    )
    return 0 if args.validate_only or passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
