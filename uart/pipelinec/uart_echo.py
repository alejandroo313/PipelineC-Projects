"""Eco UART generico: todo byte recibido por RX se reenvia por TX.

make_uart_echo(clk_mhz, baud) -> (uart_echo, ...). Combina make_uart_rx y
make_uart_tx (uart_rx.py / uart_tx.py) con una FSM de 3 estados que hace de
"pegamento" entre ambos: no sabe nada de bits ni de baudios, solo mueve
bytes de un lado a otro respetando los handshakes valid/ready.

Uso:
    uart_echo = make_uart_echo(27.0)
    @MAIN(27.0)
    def main(rx_pin: uint1_t) -> uint1_t:
        return uart_echo(rx_pin)    # -> pin TX
"""
from enum import IntEnum
from pypeline import *
from uart_common import DEFAULT_BAUD
from uart_tx import make_uart_tx
from uart_rx import make_uart_rx


def make_uart_echo(clk_mhz: float, baud: int = DEFAULT_BAUD, startup_delay_s: float = 0.05):
    """La FPGA NUNCA transmite por iniciativa propia: solo responde a un byte recibido del PC.
    Asi no hay que esperar un tiempo fijo tras configurarse (en la Tang Nano 20K, transmitir
    justo tras cargar la FPGA bloquea el puente USB BL616); es el PC quien decide cuando empezar.

    startup_delay_s: ventana corta tras la configuracion en la que se descarta lo que llegue
    por RX (en hardware se vio un byte espurio al configurar la FPGA; responderle haria que la
    FPGA transmitiera sin que el PC hubiera pedido nada). No es un retardo de "espera": el PC
    puede enviar en cuanto quiera pasada esa ventana (50 ms)."""
    boot_cycles = int(startup_delay_s * clk_mhz * 1_000_000)
    uart_send, uart_tx_t = make_uart_tx(clk_mhz, baud)
    uart_recv, uart_rx_t = make_uart_rx(clk_mhz, baud)

    @enum
    class echo_state_t(IntEnum):
        IDLE = 0   # esperando un byte del RX (y TX libre)
        SEND = 1   # tx_valid esta a 1 este ciclo: el TX lo recoge
        WAITING = 2   # TX ocupado enviando; esperar a que quede libre

    @hw_func
    def uart_echo(rx_pin: uint1_t) -> uint1_t:
        state: Reg[echo_state_t]          # sin inicializador: IDLE=0 (workaround Gowin)
        tx_data: Reg[uint8_t] = 0
        tx_valid: Reg[uint1_t] = 0
        ack: Reg[uint1_t] = 0             # "ya cogi el byte" hacia el RX
        boot: Reg[uint32_t] = 0           # ciclos desde el arranque (satura en boot_cycles)

        # Se instancian RX y TX UNA vez. Leen el valor ANTERIOR de ack /
        # tx_data / tx_valid (todavia no los hemos reasignado mas abajo).
        rx: uart_rx_t = uart_recv(rx_pin, ack)
        tx: uart_tx_t = uart_send(tx_data, tx_valid)

        ack = 0   # por defecto, pulso de 1 ciclo; se pisa a 1 al aceptar un byte

        if boot < boot_cycles:
            # Ventana de arranque: ack=1 descarta el byte espurio que pueda salir del RX.
            boot += 1
            ack = 1
            tx_valid = 0
            state = echo_state_t.IDLE
        elif state == echo_state_t.IDLE:
            if rx.rx_data_valid and tx.ready:
                tx_data = rx.rx_data     # copiamos el byte: el RX queda libre ya
                tx_valid = 1          # el TX lo vera el ciclo que viene
                ack = 1               # el RX vera ack el ciclo que viene y vuelve a IDLE
                state = echo_state_t.SEND
        elif state == echo_state_t.SEND:
            tx_valid = 0              # el TX ya lo capto: bajarlo (pulso de 1 ciclo)
            state = echo_state_t.WAITING
        else:
            if tx.ready:              # termino el bit de stop
                state = echo_state_t.IDLE

        return tx.tx_out

    return uart_echo
