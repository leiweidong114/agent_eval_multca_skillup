# slice POWER_3V3 的连接代码（subagent 产物）
# 网络 GND: ground
connect([pin(circuit.U1, 23), pin(circuit.U1, 35), pin(circuit.U2, 1), pin(circuit.U3, 1), pin(circuit.U3, 8), pin(circuit.U3, 10), pin(circuit.U4, 2)], circuit.GND)

# 网络 VIN_5V: power
connect([pin(circuit.U2, 3)], circuit.VIN_5V)

# 网络 VDD_3V3: supply
connect([pin(circuit.U2, 2), pin(circuit.U1, 24), pin(circuit.U1, 36), pin(circuit.U3, 13)], circuit.VDD_3V3)
