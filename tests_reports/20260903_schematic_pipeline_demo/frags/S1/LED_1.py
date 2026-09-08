# slice LED_1 的连接代码（subagent 产物）
# 网络 LED1_DRIVE: signal
connect([pin(circuit.U1, 11), pin(circuit.R2, 1)], circuit.LED1_DRIVE)

# 网络 LED1_ANODE: signal
connect([pin(circuit.R2, 2), pin(circuit.LED2, 2)], circuit.LED1_ANODE)
