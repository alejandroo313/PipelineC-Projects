"""Top minimo para simular el contador de ciclos (lo usa sim_metrics.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pypeline import *
from metrics import make_cycle_counter, cycles_byte

CLK_FREQ_MHZ = 27.0
cycle_counter, metrics_t = make_cycle_counter()

start_in: Input[uint1_t]
stop_in: Input[uint1_t]
idx_in: Input[uint3_t]
cycles_out: Output[uint64_t]
running_out: Output[uint1_t]
done_out: Output[uint1_t]
byte_out: Output[uint8_t]


@MAIN(CLK_FREQ_MHZ)
def main():
    m: metrics_t = cycle_counter(start_in, stop_in)
    cycles_out = m.cycles
    running_out = m.running
    done_out = m.done
    byte_out = cycles_byte(m.cycles, idx_in)
