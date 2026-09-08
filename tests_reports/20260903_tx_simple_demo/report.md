# 测试报告：简化 TX 电路（4 器件）——扩库 + 完整生成

- 日期：2026-09-03
- 目标：控制芯片 + 电源 + 功放 + 滤波器 四块构成的简化 TX 电路，先扩器件库再完整跑一遍流水线。
- 产物：`tests_reports/20260903_tx_simple_demo/`

## 一、器件库扩充（17 → 20，增量追加，未动原有器件）
| 新增符号 | 库内 library_id | 用途 | 引脚数 |
|---|---|---|---|
| RFM95W-868S2 | `RF_Module:RFM95W-868S2` | 功放/发射（SX1276 内核，SPI 受控，内含 PA，868MHz） | 16 |
| Filter_EMI_CLC | `Device:Filter_EMI_CLC` | TX 输出滤波（CLC T 型） | 3 |
| Antenna | `Device:Antenna` | RF 输出物理端点（保证输出网络不孤立） | 1 |

四块中的电源 `AMS1117-3.3`、控制芯片 `STM32F103C8Tx` 原库已有。
导入工具：`auto_layout_service/tools/add_library_symbols.py`（增量，不重建原 17 器件）。

## 二、电路方案（sheets：tx_sheets.json）
- Sheet `TX`：1 个主芯片 + 4 个功能块、10 条网络。
  - CONTROL：`U1 STM32F103C8Tx`（主芯片）
  - POWER_3V3：`U2 AMS1117-3.3`
  - RF_PA：`U3 RFM95W-868S2`
  - RF_FILTER：`U4 Filter_EMI_CLC` + `E1 Antenna`
- 链路：`U1(SPI) → U3 → RF_PA_OUT → U4 → RF_OUT → E1`；供电 `VIN_5V → U2 → VDD_3V3 → U1/U3`；GND 全接地。
- sheets 校验：skill1 校验器通过（20 个可用库器件）。

## 三、布局结果（layout/TX.json）
```
components=5   nets=10   pins=...
component_overlap_count = 0
wire_crossing_count     = 0
unrouted_net_count      = 0
wire_length             = 1770
elapsed_seconds         ≈ 1.9
```
布局参数：`iterations=60`、canvas 5600×3400、`subcircuit_min_components=1`（每个功能块独立物理区域）。

## 四、网页应用
URL：`http://127.0.0.1:8631/static_schematic/f3737b3ebf3a/shell.html`（`apply_result.json`）

## 五、说明与后续
- 功放按"真实可导入符号"选型为 RFM95W-868S2（模块内含 PA，`+20dBm`）；如需独立分立 PA/MMIC 芯片，需提供具体型号或来源符号再做第 3 次扩库。
- 若你手上有 SROC/TXVG 实际芯片符号，可走同样的 `add_library_symbols.py` 增量路径替换/叠加。
- `GET /api/auto_layout/devices` 返回仍为旧进程缓存（17）；重启服务（`start_service.py`）后即 20。
