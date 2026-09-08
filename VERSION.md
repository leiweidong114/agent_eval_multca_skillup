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

## v0.3.0（开发中）
- 新增"原理图整版生成"流水线 Skills 4 件（backend/skills/）：
  - `signal-interface-generation`：自然语言 → 多 sheet 信号接口列表 sheets.json（含校验器）。
  - `schematic-layout-codegen`：sheets → Python DSL + auto_layout 布局（器件级代码 subagent 并行 2/批）。
  - `schematic-web-apply`：布局 JSON → 多图页网页 URL。
  - `schematic-pipeline`：三步总编排。
- 配套双服务位于自动布局算法目录 `auto_layout_service/`（Service 1 auto_layout、Service 2 apply_schematic，端口 8631）。
- 端到端 demo：STM32F103C8Tx + 8×LED，指标 0 重叠/0 交叉/0 未布通。

作者：leiweidong
邮箱：leiweidong114@gmail.com
