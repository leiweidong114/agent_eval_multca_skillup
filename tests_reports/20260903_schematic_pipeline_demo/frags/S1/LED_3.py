# slice LED_3 的连接代码（subagent 产物）
# 网络 LED3_DRIVE: signal
connect([pin(circuit.U1, 13), pin(circuit.R4, 1)], circuit.LED3_DRIVE)

# 网络 LED3_ANODE: signal
connect([pin(circuit.R4, 2), pin(circuit.LED4, 2)], circuit.LED3_ANODE)
