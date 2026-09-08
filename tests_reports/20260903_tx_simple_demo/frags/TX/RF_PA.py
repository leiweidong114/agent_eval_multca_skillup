# slice RF_PA 的连接代码（subagent 产物）
# 网络 SPI_SCK: signal
connect([pin(circuit.U1, 15), pin(circuit.U3, 4)], circuit.SPI_SCK)

# 网络 SPI_MISO: signal
connect([pin(circuit.U1, 16), pin(circuit.U3, 2)], circuit.SPI_MISO)

# 网络 SPI_MOSI: signal
connect([pin(circuit.U1, 17), pin(circuit.U3, 3)], circuit.SPI_MOSI)

# 网络 SPI_NSS: signal
connect([pin(circuit.U1, 14), pin(circuit.U3, 5)], circuit.SPI_NSS)

# 网络 RFM_RESET: signal
connect([pin(circuit.U1, 11), pin(circuit.U3, 6)], circuit.RFM_RESET)
