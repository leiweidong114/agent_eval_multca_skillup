---
name: schematic-pipeline
description: 原理图整版生成总编排：把自然语言电路描述经过 ①信号接口列表生成 → ②(并行subagent)器件级代码生成+自动布局 → ③网页应用，端到端产出多图页网页 URL。要求步骤②的代码生成全部在 subagent 中完成，每批并行 2 个 subagent、逐批推进。步骤②必须运行在 subagent 内。
---

# Schematic Pipeline（原理图整版生成总编排）

## 工作方式
主 agent 担任协调者，按以下顺序调用三个子 skill。**每个子 skill 的 SKILL.md 必须先读取再执行。**

```text
用户自然语言电路描述
 → [skill1] signal-interface-generation     生成 sheets.json（多 sheet 信号接口表）
 → [skill2] schematic-layout-codegen        (subagent 并行，2/批) 逐 sheet 生成 DSL 并布局 → 布局 JSON
 → [skill3] schematic-web-apply             布局 JSON → 多图页网页 URL
 → 交付 URL + 阶段产物汇总
```

## 执行步骤

### 0. 环境检查
- 确认 `auto_layout` + `apply_schematic` 服务在 `http://127.0.0.1:8631` 可访问（`GET /api/health`）。
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
- 把用户描述与器件目录交给 skill1；产出并校验 `sheets.json`。
- 本步不拆 subagent（需要全局唯一性决策，由一个上下文完成更稳）。

### 2. 器件级代码生成 + 布局 —— 必须在 subagent 内完成（核心约束）
对每个 sheet，按 skill2 步骤执行，其中切片代码生成必须遵守：
- 先运行 `codegen_base.py` 生成 `base.txt` + `slices.json`；
- **每次只并行启动 2 个 subagent**，每个负责一个切片并产出 `<slice_id>.py`；
- 这批完成后，**再启动新的 subagent** 处理下一批（2 个切片），直到全部切片完成；
- 绝不把多个切片合并给同一个 subagent，也绝不在主上下文中手写连接代码；
- 全部完成后运行 `layout_sheet.py` 提交布局；若返回 422，把报错发给对应切片的新 subagent 修复后重提。

### 3. 网页应用 —— 主 agent 执行 skill3
- 将 `out/layout/*.json` 全部交给 skill3，渲染成多图页网页，取得 `url`。

### 4. 交付
- 向用户交付：网页 URL（主结果）+ `sheets.json`/布局 JSON/切片代码路径。
- 汇总表：sheet、器件数、网络数、overlap/crossing/unrouted、耗时、URL。
- 若某 sheet 指标不满足（overlap/unrouted 非 0），标记该图页状态为失败并说明原因与重试建议，不能静默交付。

## 约束与约定
- 器件库只读：`out/catalog.json`；任何 skill 不得创造库外器件。若需求器件库外，先问用户（扩库/替代）。
- 位号与网络名跨 skill 保持一致（契约见 skill1 `references/sheet_schema.md`）。
- step2 的 subagent 由 agent 运行时提供；若运行时不支持多 subagent，降级为单 subagent 串行并在交付说明中注明降级。

## 判定标准（交付前自检）
- [ ] 每 sheet 布局 `component_overlap_count == 0`
- [ ] 每 sheet 布局 `unrouted_net_count == 0`
- [ ] 网页可打开且图页数 = sheet 数
- [ ] 连接关系与 sheets.json 一致（位号/网络名可对照）
