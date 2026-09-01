from __future__ import annotations

from pathlib import Path

# backend/ 目录（本文件位于 backend/app/config.py -> parents[1] = backend）
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 前端生产构建目录。离线包由 FastAPI 直接托管，不要求目标机启动 Node.js。
FRONTEND_DIST = BACKEND_ROOT.parent / "frontend" / "dist"

# 评测 Skill 根目录
SKILLS_ROOT = BACKEND_ROOT / "skills"

# 所有评测产物集中在：evaluation_results/<用户>/<任务>/<时间__run_id>/
EVALUATION_RESULTS_ROOT = BACKEND_ROOT / "evaluation_results"
RUNS_ROOT = EVALUATION_RESULTS_ROOT  # compatibility alias for existing imports
