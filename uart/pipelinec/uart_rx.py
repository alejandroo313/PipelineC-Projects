from enum import IntEnum
from pypeline import *
from uart_common import DEFAULT_BAUD, clk_per_bit as _clk_per_bit


def make_uart_rx(clk_mhz: float, baud: int = DEFAULT_BAUD):
    clk_per_bit = _clk_per_bit(clk_mhz, baud)
    half_bit = clk_per_bit // 2

    @enum
    class rx_state_t(IntEnum):
        IDLE = 0
        START = 1   # Start bit
        RECV = 2    # Recibiendo bits de datos
        STOP = 3    # Stop bit
        DONE = 4    # Byte completo, esperando data_ready

    @struct
    class uart_rx_t(NamedTuple):
        rx_data: uint8_t
        rx_data_valid: uint1_t

    @hw_func
    def rx_next_state(
            state: rx_state_t,
            negedge: uint1_t,
            rx_data_ready: uint1_t,
            bit_count: uint3_t,
            cycle_cnt: uint16_t) -> rx_state_t:
        rv: rx_state_t = state
        if state == rx_state_t.IDLE:
            if negedge:
                rv = rx_state_t.START
        elif state == rx_state_t.START:
            if cycle_cnt >= clk_per_bit - 1:
                rv = rx_state_t.RECV
        elif state == rx_state_t.RECV:
            if bit_count >= 7 and cycle_cnt >= clk_per_bit - 1:
                rv = rx_state_t.STOP
        elif state == rx_state_t.STOP:
            if cycle_cnt >= half_bit - 1:
                rv = rx_state_t.DONE
        elif state == rx_state_t.DONE and rx_data_ready:
            rv = rx_state_t.IDLE
        return rv

    @hw_func
    def uart_rx(rx_in: uint1_t, rx_data_ready: uint1_t) -> uart_rx_t:
        rx_d0: Reg[uint1_t] = 1
        rx_d1: Reg[uint1_t] = 1
        rx_d2: Reg[uint1_t] = 1

        state: Reg[rx_state_t]
        bit_count: Reg[uint3_t] = 0
        cycle_cnt: Reg[uint16_t] = 0
        rx_data_latch: Reg[uint8_t] = 0

        negedge: uint1_t = rx_d2 & ~rx_d1
        rx_sync: uint1_t = rx_d1
        rx_d2 = rx_d1
        rx_d1 = rx_d0
        rx_d0 = rx_in

        o: uart_rx_t

        nxt: rx_state_t = rx_next_state(state, negedge, rx_data_ready, bit_count, cycle_cnt)

        o.rx_data = rx_data_latch
        o.rx_data_valid = (state == rx_state_t.DONE)

        if state == rx_state_t.RECV and cycle_cnt == half_bit - 1:
            new_bit: uint8_t = rx_sync
            rx_data_latch = (new_bit << 7) | (rx_data_latch >> 1)

        bit_end: uint1_t = (state == rx_state_t.RECV) and (cycle_cnt >= clk_per_bit - 1)

        if state == rx_state_t.RECV:
            if bit_end:
                bit_count += 1
        else:
            bit_count = 0

        if bit_end:
            cycle_cnt = 0
        elif nxt != state:
            cycle_cnt = 0
        else:
            cycle_cnt += 1

        state = nxt
        return o

    return uart_rx, uart_rx_t
