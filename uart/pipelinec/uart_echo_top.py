"""Ejemplo top de la UART: eco. Todo byte que el host envía por RX vuelve por TX.

Pines (Tang Nano 20K): RX = 70, TX = 69 (puente USB BL616 -> /dev/ttyUSB1). Ver uart_echo_top.cst.

Sintetizar y cargar:
    $PIPELINEC_DIR/src/pypelinec uart_echo_top.py --part "GW2AR-LV18QN88C8/I7" --pins uart_echo_top.cst

Probar desde el PC (debe devolver lo mismo que se envía):
    python3 -c "import serial; s=serial.Serial('/dev/ttyUSB1', 115200, timeout=1); s.write(b'hola'); print(s.read(4))"
"""
import os
import sys
from pypeline import *

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uart_echo import make_uart_echo

CLK_FREQ_MHZ = 27.0     # Reloj de la Tang Nano 20K
BAUD_RATE = 115200

# Ventana de arranque del eco (50 ms por defecto); el testbench la acorta para simular rapido
STARTUP_DELAY_S = float(os.environ.get("UART_STARTUP_DELAY_S", "0.05"))

uart_echo = make_uart_echo(CLK_FREQ_MHZ, BAUD_RATE, STARTUP_DELAY_S)


rx_pin: Input[uint1_t]     # Pin RX de la FPGA (entrada serie desde el PC)
tx_pin: Output[uint1_t]    # Pin TX de la FPGA (salida serie hacia el PC)


@MAIN(CLK_FREQ_MHZ)
def main():
    tx_pin = uart_echo(rx_pin)
