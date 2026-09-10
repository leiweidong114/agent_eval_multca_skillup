---
name: schematic-layout-codegen
description: 将"信号接口列表 sheets.json"翻译为可执行的 Python 电路 DSL 代码并调用 auto_layout 服务完成自动布局，输出每 sheet 一个带坐标的布局 JSON。器件级代码生成必须在多个 subagent 中并行完成（每批 2 个、逐批推进）。适用于 schematic-pipeline 的步骤二。
---

# 原理图生成 + 自动布局 (schematic-layout-codegen)

## 目标
输入 skill 一产出的 `sheets.json`，为每个 sheet 生成布局 JSON（Service 1 输出，含坐标/连线/指标）。

## 运行前
- auto_layout 服务需在 `http://127.0.0.1:8631`（env `AUTOLAYOUT_URL` 可改）。
- 以下命令一律从评测 workspace 根目录执行，不要先 `cd` 到 Skill 或 scripts 目录。将包含本文件的目录记作 `<skill-root>`，命令中的脚本必须使用该目录的绝对路径。
- 逐 sheet 处理；以下用 `--sheet <sheet_id>` 指代当前 sheet。

## 步骤 A：生成基础代码与任务切片（主 agent 执行，机械、快）
```bash
python <skill-root>/scripts/codegen_base.py --input out/sheets.json --sheet <sheet_id> --fragdir out/frags/<sheet_id>/
```
产物：
- `base.txt`：该 sheet 的基础代码——`circuit = Circuit(...)`、主芯片模板、**所有** sub_circuit 分组与器件模板、**所有** Net 对象定义（不含任何 connect）。
- `slices.json`：切片清单。每个切片 = 一个拥有至少一个待连接网络的功能组（group 或 MAIN），含关联网络及全部端点；没有网络的组不会产生空切片。
- 脚本标准输出会给出精简的切片 ID、`slices.json` 绝对路径和各输出文件绝对路径。主 agent 直接用这个摘要分派，不要再打印完整 `base.txt` 或 `slices.json` 浪费上下文。

## 步骤 B：并行 subagent 生成连接代码片段（核心，必须这样做）
1. 把切片按序两两一组；**并行启动 2 个 subagent**，每个 subagent 负责一个切片，产出 `out/frags/<sheet_id>/<slice_id>.py`。
2. subagent 任务说明（随 prompt 下发）：
   - prompt 中提供 `slices.json`、`references/python_codegen_guide.md`、目标 `.py` 的**绝对路径**；
   - subagent 只读取自己 `slice_id` 的对象，不处理其他切片；
   - 只写 **connect(...) 连接代码**，不得重复定义 `Template`/`Net`/`Circuit`；
   - 引用器件用 `circuit.<位号>`、引用网络用 `circuit.<网络名>`、引用引脚用 `pin(circuit.<位号>, <引脚号>)`；
   - `3V3`、`5V` 等数字开头的网络不是合法 Python 属性，必须写成 `circuit.__getattr__("3V3")`；其他网络仍用 `circuit.<网络名>`；
   - 输出前用 `python -c "import ast;ast.parse(open(r'<file>',encoding='utf-8').read())"` 自查语法；
   - 失败信息要回传主 agent；主 agent 等待成功后验证目标文件不再以 `# subagent` 开头。
   - Codex 的原生 subagent 通常是只读 workspace。此时用 `fork_context=false`。JustDo/OpenClaw 必须调用 `sessions_spawn`，使用 `runtime="subagent"`、`context="isolated"`，省略 `agentId` 与 `model`，禁止 `context="fork"`；这样子任务隔离父上下文，同时继承本次运行级 LiteLLM 模型。任务改为只读输入并在最终回复中返回：`FRAGMENT_BEGIN`、纯 connect 代码、`FRAGMENT_END`。主 Agent 只做机械落盘，必须逐字复制标记之间的内容，禁止自行补写/改写。
   - **`slice_id=<ID>` 必须写进 `message` 文本本身**，不能只放在 `spawn_agent.target` 等元数据字段；这些字段不会传给子任务。推荐模板：`只读 <slices绝对路径> 中 slice_id=<ID> 及 <指南绝对路径>。只处理 slice_id=<ID>，逐一生成该对象 nets 中的 connect，不得输出其他网络；用 ast.parse 在内存自查。最终仅在 FRAGMENT_BEGIN/FRAGMENT_END 之间返回代码。`
3. 一批先连续 spawn 两个（不要继承整个父上下文），再等待两个完成。等待超时使用 120 秒，若只完成一个则再次等待另一个；不要高频轮询、不要因第一次等待超时而由主 Agent 代写。主 Agent 将每个成功返回的标记区间原样写入对应 `.py`，然后运行 `ast.parse`。
4. 该批两个完成后，**再启动新 subagent 处理下一批 2 个切片**，直到本 sheet 全部切片完成。禁止一次性把全部切片交给一个 subagent。
5. 若某切片被布局校验拒绝，另起 subagent 依据服务端报错修复该切片文件，再进入步骤 C。

## 步骤 C：合并并调用布局（每 sheet 一次）
```bash
python <skill-root>/scripts/layout_sheet.py --input out/sheets.json --sheet <sheet_id> --fragdir out/frags/<sheet_id>/ --out out/layout/<sheet_id>.json [--iterations 40]
```
- 脚本把 `base.txt` + 全部 `<slice_id>.py` 合并为完整 DSL → `POST /api/auto_layout/layout`；
- 成功后把响应里的 `selected` 布局写入输出 JSON；
- 失败时打印服务端 `detail` 并退出非 0 → 回到步骤 B.4 修复对应切片。
- 脚本会先严格检查所有切片存在、不是占位、语法正确，并验证每个文件的网络集合与每个网络的端点集合都和 `slices.json` 精确一致；不允许复制整板代码到每个切片或跳过缺失切片。
- 建议 `iterations` 先用 25~40 快速校验拓扑，最终交付再提高到 60~120。

## 产物
- `out/frags/<sheet_id>/`：切片代码（中间产物，可复现并行过程）
- `out/layout/<sheet_id>.json`：布局 JSON（Service 2 / skill 三 的输入）
- 每个布局的 `metrics` 要求：`component_overlap_count = 0`、`unrouted_net_count = 0`；否则视为失败必须重跑。

## 质量要求
- 保留位号/网络名与 sheets.json 完全一致；不得改名、不得自创器件。
- 若布局返回 422 属于 sheets 语义错误，把报错原文回传给 skill 一修正 sheets，再重来。
- 布局 JSON 只能来自 `layout_sheet.py` 的服务响应，禁止模型手写或补造指标。
- 完成后回复每 sheet：布局 JSON 路径、器件数、指标（overlap/crossing/unrouted）、耗时。
