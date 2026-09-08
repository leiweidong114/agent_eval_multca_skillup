# slice RF_FILTER 的连接代码（subagent 产物）
# 网络 RF_PA_OUT: signal
connect([pin(circuit.U3, 9), pin(circuit.U4, 1)], circuit.RF_PA_OUT)

# 网络 RF_OUT: signal
connect([pin(circuit.U4, 3), pin(circuit.E1, 1)], circuit.RF_OUT)
