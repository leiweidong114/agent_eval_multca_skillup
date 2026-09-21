from __future__ import annotations

from pathlib import Path

from agent_eval.results_paths import evaluation_results_root

# backend/ 目录（本文件位于 backend/app/config.py -> parents[1] = backend）
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 评测 Skill 根目录
SKILLS_ROOT = BACKEND_ROOT / "skills"

# 所有评测产物集中在：evaluation_results/<用户>/<任务>/<时间__run_id>/
EVALUATION_RESULTS_ROOT = evaluation_results_root(BACKEND_ROOT)
RUNS_ROOT = EVALUATION_RESULTS_ROOT  # compatibility alias for existing imports
