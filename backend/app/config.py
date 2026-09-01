from __future__ import annotations

from pathlib import Path

# backend/ 目录（本文件位于 backend/app/config.py -> parents[1] = backend）
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 评测 Skill 根目录
SKILLS_ROOT = BACKEND_ROOT / "skills"

# 评测输出目录
RUNS_ROOT = BACKEND_ROOT / "runs"
