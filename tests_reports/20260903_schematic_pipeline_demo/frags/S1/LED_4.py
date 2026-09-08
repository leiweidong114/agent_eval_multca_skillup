# slice LED_4 的连接代码（subagent 产物）
# 网络 LED4_DRIVE: signal
connect([pin(circuit.U1, 14), pin(circuit.R5, 1)], circuit.LED4_DRIVE)

# 网络 LED4_ANODE: signal
connect([pin(circuit.R5, 2), pin(circuit.LED5, 2)], circuit.LED4_ANODE)
