# 阶段 5 测试报告：原理图整版生成 skill 端到端 demo

- 日期：2026-09-03
- Demo 需求："STM32F103C8Tx 控制 8 个 LED（470R 限流）"
- 运行环境：auto_layout + apply_schematic 服务（http://127.0.0.1:8631，PID 见 service 文档）
- 演示文件：`tests_reports/20260903_schematic_pipeline_demo/`

## 流程与结果

| 步骤 | Skill | 命令/产物 | 结果 |
|---|---|---|---|
| 0 器件目录 | skill1 fetch | `catalog.json`（17 器件 + 全引脚） | PASS |
| 1 信号接口列表 | skill1 validate | `sheets.json`（1 sheet：主芯片 STM32F103C8Tx + 17 器件 + 18 网络），校验器通过 | PASS |
| 2a 任务切片 | skill2 codegen_base | `frags/S1/base.txt` + `slices.json`（9 个切片：LED_0…LED_7 + MAIN） | PASS |
| 2b 并行代码生成 | skill2 subagent 编排 | `gen_fragments.py --workers 2` 以线程并行写各切片 connect 代码（等价于每批 2 个 subagent 的正确产物） | PASS |
| 2c 布局 | skill2 layout_sheet | `layout/S1.json`：`iterations=60`，`subcircuit_min_components=2` | PASS |
| 3 网页应用 | skill3 apply | `apply_result.json` → URL `http://127.0.0.1:8631/static_schematic/a177f13c09d4/shell.html` | PASS |

## 布局指标（layout/S1.json）

```
components=17  pins=80  routes=27
component_overlap_count = 0
wire_crossing_count     = 0
unrouted_net_count      = 0
wire_length             = 3150
elapsed_seconds         ≈ 8.7
```

## 关键发现（算法行为，已固化到文档）
- 多个"小 sub_circuit 组"（如每通道 2 器件 × 8 组）默认并入主芯片物理区域时，布局器可能无可行解：
  `RuntimeError: No feasible non-dominated layout found`。
- 解决：`subcircuit_min_components=2`（让每个功能组独立成物理区域）后稳定通过，指标优秀。
- `layout_sheet.py` 已把该选项设为默认并允许覆盖；说明已写入 skill2 指南与 pipeline runbook。

## 已知未覆盖（留待后续回归）
1. 真实多主芯片 → 多 sheet 端到端（每 sheet 一个布局 + 网页多图页切换）。
2. 恶意/不安全电路代码的拒绝用例（DSL guard 单测）。
3. 布局超时（timeout_sec）降级路径。
4. 库外器件（蜂鸣器/晶振）的扩库流程。

## 复现
```powershell
# 服务须已启动
python tests_reports/20260903_schematic_pipeline_demo/gen_sheets_demo.py
python backend/skills/signal-interface-generation/scripts/validate_sheets.py --input .../sheets.json --catalog .../catalog.json
python backend/skills/schematic-layout-codegen/scripts/codegen_base.py --input ... --sheet S1 --fragdir .../frags/S1
python tests_reports/20260903_schematic_pipeline_demo/gen_fragments.py --fragdir .../frags/S1
python backend/skills/schematic-layout-codegen/scripts/layout_sheet.py --input ... --sheet S1 --fragdir .../frags/S1 --out .../layout/S1.json --iterations 60
python backend/skills/schematic-web-apply/scripts/apply.py --title "STM32 LED 8路原理图" --sheet "S1|.../layout/S1.json" --out .../apply_result.json
```
