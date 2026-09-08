# slice LED_6 的连接代码（subagent 产物）
# 网络 LED6_DRIVE: signal
connect([pin(circuit.U1, 16), pin(circuit.R7, 1)], circuit.LED6_DRIVE)

# 网络 LED6_ANODE: signal
connect([pin(circuit.R7, 2), pin(circuit.LED7, 2)], circuit.LED6_ANODE)
