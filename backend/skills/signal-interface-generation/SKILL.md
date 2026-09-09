---
name: signal-interface-generation
description: 根据用户对原理图的功能/器件自然语言描述，生成"信号接口列表"：把电路按主芯片拆分为多个 sheet，每个 sheet 包含一个主芯片 block 及其外围器件的全部引脚连接（输入器件、输出器件、输入引脚、输出引脚、网络名），输出 sheets.json 与可读表格，并做器件库一致性校验。适用于 schematic-pipeline 的步骤一。
---

# 信号接口列表生成 (signal-interface-generation)

## 目标
把一段自然语言电路描述（例如"做一个 STM32F103C8Tx 控制 8 个 LED 的电路"）转成结构化、无歧义、可进入自动布局流水线的 **sheets.json** 和每 sheet 一页的可读表格。

## 前置
1. 确保 auto_layout 服务已启动：`http://127.0.0.1:8631`（见 `api_reference`）。
2. 所有命令都从评测 workspace 根目录执行，不要先 `cd` 到 Skill 或 scripts 目录。用 `python skills/signal-interface-generation/scripts/fetch_catalog.py --out out/catalog.json --with-pins` 拉取器件库目录，确认可引用器件的 `library_id` 与引脚号。**绝不允许使用库外器件。**
   - `catalog.json` 可能很大，禁止用 `read` 或终端命令把整个文件载入模型上下文；必须用短脚本按器件关键字筛选，只输出本任务实际使用的候选器件、`library_id` 和引脚。
   - 库外器件（如蜂鸣器、晶振）不允许凭空引用；如果描述需要但库内没有，向用户说明并改用最接近的替代（例：蜂鸣器→LED+三极管示意）或要求扩库。

## 执行步骤
1. 阅读用户描述，识别所有主芯片（引脚 > 10 的 SoC/MCU 自动成为算法主芯片）与外围功能块。
2. **按主芯片拆 sheet**：每个主芯片是一个 block，放一个 sheet；sheet 内列它的全部外围器件（位号、library_id、值、所属功能组）和全部连接行。
3. 一个连接行即信号接口表中的一行，必须包含：
   `(输入器件, 输入引脚, 输出器件, 输出引脚, 网络名)`；电源/地连接也按同样行表达，`网络名` 用 GND/VDD_3V3 等大写规范名。
4. 汇总每个 sheet 的 `nets`（网络），网络名全局唯一且只由 `A-Z 0-9 _` 组成、不得数字开头。
5. 把结果写入 `<output>/sheets.json`（schema 见 `references/sheet_schema.md`），并同步生成每 sheet 一页的可读表格 `<output>/sheets_markdown/S1_<sheet>.md`（标题 + 该主芯片的器件清单 + 上表格式的连接表）。
6. 从 workspace 根目录运行 `python skills/signal-interface-generation/scripts/validate_sheets.py --input out/sheets.json --catalog out/catalog.json` 校验；**不通过必须修正后再交付**。

## 表格列（用户可见契约）
每个 sheet 表格包含这些列，顺序固定：
| 输入器件 | 输入引脚号 | 输出器件 | 输出引脚号 | 网络名 | 说明 |
其中的"说明"只做备注（如去耦/上拉），不用于连接定义。

## 关键规则
- 同一网络只允许被"正向"定义一次（规则：小位号器件作为输入侧，避免 A→B 与 B→A 两行重复定义同一条连接）。
- 每个外围器件必须属于某个 `group`（功能子电路，如 `LED_0`、`RESET`）；主芯片不属于任何 group。
- 器件位号全板唯一；引脚下标必须是库内真实引脚号（`number`）。
- 用户要求是多个独立模块时，给每个模块独立 sheet；sheet 间若需要互连，用"同名网络 + 两端各只连本 sheet 引脚"表达（即跨页网络标签）。
- 完成后回复：总 sheet 数、各 sheet 器件数/网络数、校验结果。
