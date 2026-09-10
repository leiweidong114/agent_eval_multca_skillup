---
name: schematic-pipeline
description: 原理图整版生成总编排：把自然语言电路描述经过 ①信号接口列表生成 → ②(subagent)器件级代码生成+自动布局 → ③网页应用，端到端产出多图页网页 URL。要求步骤②的代码生成全部在独立 subagent 中完成；普通运行时每批最多并行 2 个，JustDo/OpenClaw 顺序执行以兼容低并发模型网关。
---

# Schematic Pipeline（原理图整版生成总编排）

## 工作方式
主 agent 担任协调者，按以下顺序调用三个子 skill。**开始任何阶段前，先在一次读取操作中读完本 Skill 以及三个子 Skill 的 SKILL.md；不得等到最后一步才补读，也不得仅凭总编排摘要调用脚本。**
当本 Skill 位于评测 bundle 内时，三个子 Skill 是 bundle 中的文件，不是三个额外注册的 Skill 工具；按 bundle 顶层 `SKILL.md` 给出的精确路径读取。所有脚本均以其所在 Skill 目录的绝对路径调用。

```text
用户自然语言电路描述
 → [skill1] signal-interface-generation     生成 sheets.json（多 sheet 信号接口表）
 → [skill2] schematic-layout-codegen        (独立 subagent，最多 2/批) 逐 sheet 生成 DSL 并布局 → 布局 JSON
 → [skill3] schematic-web-apply             布局 JSON → 多图页网页 URL
 → 交付 URL + 阶段产物汇总
```

## 执行步骤

### 0. 环境检查
- 确认 `auto_layout` + `apply_schematic` 服务在 `http://127.0.0.1:8631` 可访问（`GET /api/health`）。
- Windows 运行时使用 PowerShell，不要使用 `ls -la`、`find`、`head`、`mkdir -p` 等 Unix 命令。
- 整个任务始终停留在评测 workspace 根目录，禁止 `cd` 到 bundle、子 Skill 或 `scripts`。先用 `$bundleRoot = (Resolve-Path '.agents/skills/<bundle-name>').Path` 保存绝对路径，再按 `python "$bundleRoot/skills/<child>/scripts/<script>.py" --out out/...` 调用；脚本若打印出 workspace 之外的输出路径，该次调用无效，必须纠正后重跑。
- 建立输出目录骨架：
```
out/
├── catalog.json            # fetch_catalog.py 拉取（供 step1 校验）
├── sheets.json             # step1 产物
├── sheets_markdown/        # step1 可读表格（每 sheet 一页）
├── frags/<sheet_id>/       # step2 中间产物（base.txt + slices + 各切片代码）
└── layout/<sheet_id>.json  # step2 布局 JSON
└── apply_result.json       # step3 网页结果
```

### 1. 信号接口列表生成 —— 主 agent 直接执行 skill1
- 先读 skill1 的 `references/sheet_schema.md`，再把用户描述与器件目录交给 skill1；产出并校验 `sheets.json`。校验退出码非 0 时不得进入任何后续阶段，只能修正并重验。
- 本步不拆 subagent（需要全局唯一性决策，由一个上下文完成更稳）。

### 2. 器件级代码生成 + 布局 —— 必须在 subagent 内完成（核心约束）
对每个 sheet，按 skill2 步骤执行，其中切片代码生成必须遵守：
- 先运行 `codegen_base.py` 生成 `base.txt` + `slices.json`；
- 每个切片必须交给一个独立 subagent；普通运行时每批最多并行 2 个，JustDo/OpenClaw 为兼容低并发 LiteLLM 上游，必须保持同一时间只有 1 个子任务活动：spawn 一个、`sessions_yield` 收到终态结果后，再 spawn 下一个；
- 当前批次完成后，再启动新的 subagent，直到全部切片完成；
- 绝不把多个切片合并给同一个 subagent，也绝不在主上下文中手写连接代码；
- 全部完成后运行 `layout_sheet.py` 提交布局；若返回 422，把报错发给对应切片的新 subagent 修复后重提。
- Codex 原生 subagent 可能是只读 workspace：用 `fork_context=false` 启动。JustDo/OpenClaw 必须调用 `sessions_spawn`，参数使用 `runtime="subagent"`、`context="isolated"`，并且省略 `agentId` 与 `model`；禁止使用 `context="fork"`，从而既隔离父任务上下文，又让子任务继承本次运行级 LiteLLM 模型。两种运行时都必须在任务正文写出具体 `slice_id`，给出切片与指南的绝对路径，让子任务在最终回复的 `FRAGMENT_BEGIN`/`FRAGMENT_END` 之间返回代码；主 Agent 只可把该区间**原样**写入目标文件，不可自行生成或修改连接代码。其他可写运行时允许 subagent 直接写目标文件。
- 普通运行时一批可先连续启动两个 subagent，再用一次较长等待（建议 120 秒）收集；JustDo/OpenClaw 每次只启动一个并立即 `sessions_yield`，收到该子任务终态结果后才启动下一个。未完成则继续等待，不要在 30 秒后猜测失败。检查每个子任务状态、返回代码/目标文件和 `ast.parse`。任何 subagent 失败都不得由主 Agent 自己补写片段冒充完成。

### 3. 网页应用 —— 主 agent 执行 skill3
- 将 `out/layout/*.json` 全部交给 skill3，渲染成多图页网页，取得 `url`。

### 4. 交付
- 向用户交付：网页 URL（主结果）+ `sheets.json`/布局 JSON/切片代码路径。
- 最终回复先原样列出 `schematic-pipeline: used`、`signal-interface-generation: used`、`schematic-layout-codegen: used`、`schematic-web-apply: used` 四行，确保评测能够区分总编排 Skill 和三个执行子 Skill。
- 汇总表：sheet、器件数、网络数、overlap/crossing/unrouted、耗时、URL。
- 若某 sheet 指标不满足（overlap/unrouted 非 0），标记该图页状态为失败并说明原因与重试建议，不能静默交付。
- 只有真实运行 `fetch_catalog.py`、`validate_sheets.py`、`render_sheets_markdown.py`、`codegen_base.py`、`layout_sheet.py` 和 `apply.py` 且全部退出码为 0，才可声明成功。禁止手写 `catalog.json`、布局 JSON、`apply_result.json` 或虚构 URL/指标。

## 约束与约定
- 器件库只读：`out/catalog.json`；任何 skill 不得创造库外器件。若需求器件库外，先问用户（扩库/替代）。
- 位号与网络名跨 skill 保持一致（契约见 skill1 `references/sheet_schema.md`）。
- step2 的 subagent 由 agent 运行时提供。若任务明确要求验证 subagent 而运行时不支持，必须判定失败；普通交付任务才允许降级为单 subagent 串行并明确说明。

## 判定标准（交付前自检）
- [ ] 每 sheet 布局 `component_overlap_count == 0`
- [ ] 每 sheet 布局 `unrouted_net_count == 0`
- [ ] 网页可打开且图页数 = sheet 数
- [ ] 连接关系与 sheets.json 一致（位号/网络名可对照）
- [ ] `out/frags/<sheet_id>/` 中每个 `slices.json` 条目都有非占位 `.py`，且确由成功的 subagent 生成
- [ ] `out/apply_result.json.url_verification.status == "ok"`
