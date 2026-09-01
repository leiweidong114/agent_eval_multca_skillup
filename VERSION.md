# 版本记录

## v0.1.0（dev 分支）
- 初始版本：本地、无登录、无数据库、无 LiteLLM 依赖的 Agent Skill 评测 CLI 工具。
- 使用 Skill-Up + Multica 本地引擎，支持确定性评分（task_score / baseline_score / skill_gain / execution_stability）。

## v0.2.0（server_dev 分支，当前）
- 前后端分离改造：
  - 现有 Python 评测代码迁入 `backend/` 目录。
  - 后端新增 FastAPI Web 服务，暴露 REST 接口（/api/agents、/api/skills、/api/run、/api/runs 等）。
  - 前端新增 Vue 3 + Vite + Element Plus 界面（评测运行、评测结果、Skill/Agent 管理）。
- 核心评测逻辑不变，仅增加 Web 交互层。

作者：leiweidong
邮箱：leiweidong114@gmail.com
