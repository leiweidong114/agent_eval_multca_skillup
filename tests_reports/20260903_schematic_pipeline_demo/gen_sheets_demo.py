# -*- coding: utf-8 -*-
"""Generate the STM32 + 8xLED demo sheets.json (dev-test fixture)."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
U1_PINS = ["10", "11", "12", "13", "14", "15", "16", "17", "23", "24", "35", "36"]
GPIO_PIN = ["10", "11", "12", "13", "14", "15", "16", "17"]

components = [{
    "instance": "U1",
    "library_id": "MCU_ST_STM32F1:STM32F103C8Tx",
    "value": "STM32F103C8Tx",
    "pins": U1_PINS,
    "group": "",
    "is_main": True,
}]
nets = []

for index in range(8):
    group = f"LED_{index}"
    r_name, led_name = f"R{index + 1}", f"LED{index + 1}"
    components.append({"instance": r_name, "library_id": "Device:R",
                       "value": "470R", "pins": ["1", "2"], "group": group,
                       "is_main": False})
    components.append({"instance": led_name, "library_id": "Device:LED",
                       "value": "LED_RED", "pins": ["1", "2"], "group": group,
                       "is_main": False})
    nets.append({"name": f"LED{index}_DRIVE", "type": "signal", "priority": 3,
                 "pins": [{"instance": "U1", "pin": GPIO_PIN[index]},
                          {"instance": r_name, "pin": "1"}]})
    nets.append({"name": f"LED{index}_ANODE", "type": "signal", "priority": 3,
                 "pins": [{"instance": r_name, "pin": "2"},
                          {"instance": led_name, "pin": "2"}]})

nets.append({"name": "VDD_3V3", "type": "supply", "priority": 5,
             "pins": [{"instance": "U1", "pin": "24"}, {"instance": "U1", "pin": "36"}]})
gnd_pins = [{"instance": "U1", "pin": "23"}, {"instance": "U1", "pin": "35"}]
gnd_pins += [{"instance": f"LED{i + 1}", "pin": "1"} for i in range(8)]
nets.append({"name": "GND", "type": "ground", "priority": 5, "pins": gnd_pins})

sheets = {
    "schema_version": "1",
    "project": "stm32_led_demo",
    "description": "STM32F103C8Tx 控制 8 个 LED（限流 470R），单主芯片单 sheet。",
    "sheets": [{
        "sheet_id": "S1",
        "title": "STM32F103C8Tx_LED",
        "main_chip": {"instance": "U1", "library_id": "MCU_ST_STM32F1:STM32F103C8Tx"},
        "components": components,
        "nets": nets,
    }],
}
(HERE / "sheets.json").write_text(json.dumps(sheets, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
print("components:", len(components), "nets:", len(nets))
