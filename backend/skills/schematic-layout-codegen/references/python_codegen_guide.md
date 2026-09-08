# Python 电路 DSL 生成指南（subagent 必读）

布局服务在受限子进程里执行一段 Python，运行环境中已注入以下名称（**不要 import**）：

| 名称 | 用途 |
|---|---|
| `Circuit(name)` | 创建电路对象，必须赋给变量 `circuit` |
| `Template(instance, library_id, value, pin_list)` | 声明一个器件实例 |
| `Net(name, type, priority)` | 创建网络对象，用 `circuit.add(Net(...))` 加入 |
| `connect(pin_or_list, net)` | 把引脚加入网络 |
| `pin(component, number)` | 引用 `component.<引脚号>` → 引脚引用对象 |
| `component(circuit, "R1")` | 按位号取器件对象 |
| `circuit.<位号>` / `circuit.<网络名>` | 属性式引用已定义器件/网络 |

## 代码结构（由 codegen_base.py 生成，subagent 不需要写）
1. `circuit = Circuit("项目名")`
2. 主芯片：`circuit.add(Template("U1", "<library_id>", "<值>", ["脚1","脚2",...]))`
3. 功能组：`with circuit.sub_circuit("组名"): circuit.add(Template("R1", "Device:R", "470R", ["1","2"]))`
4. 网络对象定义（空引脚，connect 由切片代码填充）：`circuit.add(Net("GND","ground",5))`

## subagent 只写 connect 片段
1. 不得重复 `Circuit/Template/Net/with sub_circuit`。
2. 引脚一律用**数字引脚号**（主芯片多引脚器件名称可能重复，如 VSS，禁止用名称）。
3. 例：
```python
# 网络 LED0_DRIVE 连接 主芯片 PA0(脚10) 与 R1.1
connect([pin(circuit.U1, 10), pin(circuit.R1, 1)], circuit.LED0_DRIVE)
# 网络 GND 连接 LED1 阴极(脚1)
connect([pin(circuit.LED1, 1)], circuit.GND)
```
4. 网络对象、器件对象已在 base 中定义，直接 `circuit.<NAME>` 引用；位号/网络名从切片 JSON 里原样抄。
5. 每行网络 connect 前用 `# 网络 <NAME>: <简要电气说明>` 注释，方便人工复核。
6. 一个器件引脚可多次出现在不同 connect（同引脚连到不同网络会由服务端去重/报错），但每个切片的 `pins` 字段已把网络—引脚关系给全，按图索骥即可。

## 语法自查
```powershell
python -c "import ast; ast.parse(open(r'<你的输出文件>', encoding='utf-8').read()); print('OK')"
```

## 服务端常见 422 及含义
| 报错关键字 | 原因与修法 |
|---|---|
| `不在器件库` / `Unknown schematic symbol` | library_id 错误，回查 /devices |
| `ambiguous` / `missing pins` | 引脚引用错（多半用了重名 name 而非 number）|
| `必须属于一个子电路` | 器件没进 sub_circuit 组（subagent 不该出现——属于 base 职责）|
| `Duplicate circuit name` | Template 或 Net 名字重复，回查 sheets |
| `Networks not connected to a main component` | 网络没有追溯到主芯片，多数是漏 connect 或端点笔误 |
| `No feasible non-dominated layout found` | 多个小型 sub_circuit（每组长 <2~5）挤在主芯片域内时布局器常无解。`layout_sheet.py` 已默认 `subcircuit_min_components=2` 让每个功能组独立成区；仍失败就把相近的小组并入一个组 |
| 布局指标 `unrouted_net_count>0` | 连线拥塞/参数问题，可先增大 canvas 或提高 iterations 再试 |

> 经验：sub_circuit 分组请按"功能模块"而不是"单器件"组织（如 8 路 LED 归入 `LED_DRIVER`，或每组 ≥2 器件并开启独立分区）。
