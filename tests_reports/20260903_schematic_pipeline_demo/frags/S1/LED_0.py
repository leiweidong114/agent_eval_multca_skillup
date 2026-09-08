# slice LED_0 的连接代码（subagent 产物）
# 网络 LED0_DRIVE: signal
connect([pin(circuit.U1, 10), pin(circuit.R1, 1)], circuit.LED0_DRIVE)

# 网络 LED0_ANODE: signal
connect([pin(circuit.R1, 2), pin(circuit.LED1, 2)], circuit.LED0_ANODE)

# 网络 GND: ground
connect([pin(circuit.U1, 23), pin(circuit.U1, 35), pin(circuit.LED1, 1), pin(circuit.LED2, 1), pin(circuit.LED3, 1), pin(circuit.LED4, 1), pin(circuit.LED5, 1), pin(circuit.LED6, 1), pin(circuit.LED7, 1), pin(circuit.LED8, 1)], circuit.GND)
