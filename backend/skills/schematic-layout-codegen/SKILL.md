---
name: schematic-layout-codegen
description: 将"信号接口列表 sheets.json"翻译为可执行的 Python 电路 DSL 代码并调用 auto_layout 服务完成自动布局，输出每 sheet 一个带坐标的布局 JSON。器件级代码生成必须在多个 subagent 中并行完成（每批 2 个、逐批推进）。适用于 schematic-pipeline 的步骤二。
---

# 原理图生成 + 自动布局 (schematic-layout-codegen)

## 目标
输入 skill 一产出的 `sheets.json`，为每个 sheet 生成布局 JSON（Service 1 输出，含坐标/连线/指标）。

## 运行前
- auto_layout 服务需在 `http://127.0.0.1:8631`（env `AUTOLAYOUT_URL` 可改）。
- 逐 sheet 处理；以下用 `--sheet <sheet_id>` 指代当前 sheet。

## 步骤 A：生成基础代码与任务切片（主 agent 执行，机械、快）
```bash
python scripts/codegen_base.py --input sheets.json --sheet <sheet_id> --fragdir out/frags/<sheet_id>/
```
产物：
- `base.txt`：该 sheet 的基础代码——`circuit = Circuit(...)`、主芯片模板、**所有** sub_circuit 分组与器件模板、**所有** Net 对象定义（不含任何 connect）。
- `slices.json`：切片清单。每个切片 = 一个功能组（group 或 MAIN），含该组器件与其关联网络（含主芯片端点）。
- 主 agent 读取 `slices.json`，把每个切片作为一份"子任务"交给一个 subagent。

## 步骤 B：并行 subagent 生成连接代码片段（核心，必须这样做）
1. 把切片按序两两一组；**并行启动 2 个 subagent**，每个 subagent 负责一个切片，产出 `out/frags/<sheet_id>/<slice_id>.py`。
2. subagent 任务说明（随 prompt 下发）：
   - 阅读切片 JSON 与 `references/python_codegen_guide.md`；
   - 只写 **connect(...) 连接代码**，不得重复定义 `Template`/`Net`/`Circuit`；
   - 引用器件用 `circuit.<位号>`、引用网络用 `circuit.<网络名>`、引用引脚用 `pin(circuit.<位号>, <引脚号>)`；
   - 输出前用 `python -c "import ast;ast.parse(open(r'<file>',encoding='utf-8').read())"` 自查语法；
   - 失败信息要回传主 agent。
3. 该批两个完成后，**再启动新 subagent 处理下一批 2 个切片**，直到本 sheet 全部切片完成。禁止一次性把全部切片交给一个 subagent。
4. 若某切片被布局校验拒绝，另起 subagent 依据服务端报错修复该切片文件，再进入步骤 C。

## 步骤 C：合并并调用布局（每 sheet 一次）
```bash
python scripts/layout_sheet.py --input sheets.json --sheet <sheet_id> --fragdir out/frags/<sheet_id>/ --out out/layout/<sheet_id>.json [--iterations 40]
```
- 脚本把 `base.txt` + 全部 `<slice_id>.py` 合并为完整 DSL → `POST /api/auto_layout/layout`；
- 成功后把响应里的 `selected` 布局写入输出 JSON；
- 失败时打印服务端 `detail` 并退出非 0 → 回到步骤 B.4 修复对应切片。
- 建议 `iterations` 先用 25~40 快速校验拓扑，最终交付再提高到 60~120。

## 产物
- `out/frags/<sheet_id>/`：切片代码（中间产物，可复现并行过程）
- `out/layout/<sheet_id>.json`：布局 JSON（Service 2 / skill 三 的输入）
- 每个布局的 `metrics` 要求：`component_overlap_count = 0`、`unrouted_net_count = 0`；否则视为失败必须重跑。

## 质量要求
- 保留位号/网络名与 sheets.json 完全一致；不得改名、不得自创器件。
- 若布局返回 422 属于 sheets 语义错误，把报错原文回传给 skill 一修正 sheets，再重来。
- 完成后回复每 sheet：布局 JSON 路径、器件数、指标（overlap/crossing/unrouted）、耗时。
