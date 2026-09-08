# sheets.json —— 信号接口列表数据契约（原理图整版生成 · 步骤一产物）

```json
{
  "schema_version": "1",
  "project": "stm32_led_demo",
  "description": "自然语言需求的一句话摘要",
  "sheets": [
    {
      "sheet_id": "S1",
      "title": "STM32F103C8Tx_LED",
      "main_chip": {"instance": "U1", "library_id": "MCU_ST_STM32F1:STM32F103C8Tx"},
      "components": [
        {
          "instance": "U1",
          "library_id": "MCU_ST_STM32F1:STM32F103C8Tx",
          "value": "STM32F103C8Tx",
          "pins": ["10", "11", "23", "24", "35", "36"],
          "group": "",
          "is_main": true
        },
        {
          "instance": "R1",
          "library_id": "Device:R",
          "value": "470R",
          "pins": ["1", "2"],
          "group": "LED_0",
          "is_main": false
        },
        {
          "instance": "LED1",
          "library_id": "Device:LED",
          "value": "LED_RED",
          "pins": ["1", "2"],
          "group": "LED_0",
          "is_main": false
        }
      ],
      "nets": [
        {
          "name": "GND",
          "type": "ground",
          "priority": 5,
          "pins": [
            {"instance": "U1", "pin": "23"},
            {"instance": "U1", "pin": "35"},
            {"instance": "LED1", "pin": "1"}
          ]
        },
        {
          "name": "LED0_DRIVE",
          "type": "signal",
          "priority": 3,
          "pins": [
            {"instance": "U1", "pin": "10"},
            {"instance": "R1", "pin": "1"}
          ]
        },
        {
          "name": "LED0_ANODE",
          "type": "signal",
          "priority": 3,
          "pins": [
            {"instance": "R1", "pin": "2"},
            {"instance": "LED1", "pin": "2"}
          ]
        }
      ]
    }
  ]
}
```

## 字段语义
- `components[].pins`：该器件实际被引用引脚号**全集**（主芯片只列用到脚，不列全 48 脚；二端器件恒为 `["1","2"]`）。
- `components[].group`：功能子电路名，非主器件必须非空，同组器件最终会进入同一 `sub_circuit`。
- `nets[].type`：`ground / power / supply / signal / analog / clock / critical` 之一；`priority` 1-5（越大越关键）。
- `nets[].pins`：该网络全部端点（≥2）。同一网络只出现在一个 sheet 中；跨 sheet 需要在另一个 sheet 内以同名网络 + 单端 stub 表达（`pins` 可允许 1 端）。

## 一致性校验（validate_sheets.py 规则）
1. 位号唯一；`library_id` 存在于器件库；引脚号存在于该器件定义。
2. 每个非主器件都归入某个 group；组内器件与 nets 关联能追溯到主芯片（连通性交由布局服务校验）。
3. 网络名合法（`^[A-Z][A-Z0-9_]*$`），全局不重复。
4. 连接无自环（同一器件两脚连到自己不算错但会被标记告警）。
