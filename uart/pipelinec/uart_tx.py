from enum import IntEnum
from pypeline import *
from uart_common import DEFAULT_BAUD, clk_per_bit as _clk_per_bit


def make_uart_tx(clk_mhz: float, baud: int = DEFAULT_BAUD):
    clk_per_bit = _clk_per_bit(clk_mhz, baud)

    @enum
    class tx_state_t(IntEnum):
        IDLE = 0
        START = 1   # Start bit
        SEND = 2    # Bits de datos
        STOP = 3    # Stop bit

    @struct
    class uart_tx_t(NamedTuple):
        tx_out: uint1_t
        ready: uint1_t      # 1 = el TX esta libre y puede aceptar un byte

    @hw_func
    def tx_next_state(state: tx_state_t, tx_data_valid: uint1_t, bit_count: uint3_t, tick_done: uint1_t) -> tx_state_t:
        rv: tx_state_t = state
        if state == tx_state_t.IDLE and tx_data_valid:
            rv = tx_state_t.START
        elif state == tx_state_t.START and tick_done:
            rv = tx_state_t.SEND
        elif state == tx_state_t.SEND and tick_done and bit_count >= 7:
            rv = tx_state_t.STOP
        elif state == tx_state_t.STOP and tick_done:
            rv = tx_state_t.IDLE
        return rv

    @hw_func
    def uart_tx(tx_data: uint8_t, tx_data_valid: uint1_t) -> uart_tx_t:
        state: Reg[tx_state_t]
        bit_count: Reg[uint3_t] = 0
        clk_count: Reg[uint16_t] = 0
        tx_out: Reg[uint1_t] = 1
        tx_data_latch: Reg[uint8_t] = 0

        o: uart_tx_t

        tick_done: uint1_t = (clk_count >= (clk_per_bit - 1))
        nxt: tx_state_t = tx_next_state(state, tx_data_valid, bit_count, tick_done)

        # Protocolo valid/ready estandar: el byte se acepta en el ciclo en que
        # tx_data_valid y ready valen 1 a la vez (solo posible en IDLE).
        if state == tx_state_t.IDLE and tx_data_valid:
            tx_data_latch = tx_data

        if state == tx_state_t.IDLE:
            o.ready = 1
        else:
            o.ready = 0

        if state == tx_state_t.SEND and tick_done:
            clk_count = 0
            bit_count += 1
        elif nxt != state:
            clk_count = 0
        else:
            clk_count += 1

        if state == tx_state_t.STOP:
            tx_out = 1
        elif state == tx_state_t.START:
            tx_out = 0
        elif state == tx_state_t.SEND:
            tx_out = (tx_data_latch >> bit_count) & 0x1
        else:
            tx_out = 1

        state = nxt
        o.tx_out = tx_out
        return o

    return uart_tx, uart_tx_t
