"""Contador de ciclos de reloj para medir el tiempo de un programa con el reloj de la FPGA.

Modulo independiente: no sabe nada de UART ni de ningun proyecto. Se acopla a cualquier diseno con
dos pulsos (start, stop) y se lee un valor de 64 bits. Tiempo real = ciclos / frecuencia del reloj
(@MAIN), NO / fmax.

Uso en un diseno:

    sys.path.insert(0, ".../common")
    from metrics import make_cycle_counter, cycles_byte

    cycle_counter, metrics_t = make_cycle_counter()

    @MAIN(CLK_FREQ_MHZ)
    def main():
        ...
        m: metrics_t = cycle_counter(start_pulse, stop_pulse)
        # m.cycles  : uint64_t  ciclos medidos (se mantiene congelado tras stop)
        # m.running : uint1_t   1 mientras se esta contando
        # m.done    : uint1_t   1 durante el ciclo en que se registra stop (cycles ya es el definitivo)

        # serializar los 8 bytes (little-endian) tras el resultado:
        tx_data = cycles_byte(frame_cycles, byte_idx)    # byte_idx 0..7

Semantica (start/stop en el ciclo t_start / t_stop):
  * start con el contador parado: pone la cuenta a 0 y empieza a contar. Ignorado si ya cuenta
    (la primera trama gana: se puede conectar start a "cada byte recibido" sin cuidado).
  * stop con el contador contando: lo detiene y congela. Ignorado si esta parado.
  * start y stop en el mismo ciclo con el contador parado: se ignoran (medicion de 0 ciclos no valida).
  * cycles = t_stop - t_start (stop 5 ciclos despues de start -> 5).
  * Un nuevo start tras un stop reinicia la cuenta.
"""
from pypeline import *


def make_cycle_counter():
    @struct
    class metrics_t(NamedTuple):
        cycles: uint64_t
        running: uint1_t
        done: uint1_t

    @hw_func
    def cycle_counter(start: uint1_t, stop: uint1_t) -> metrics_t:
        count: Reg[uint64_t] = 0
        running: Reg[uint1_t] = 0

        # Valores registrados de este ciclo (antes de asignar: tras asignar un Reg su nombre ya
        # vale el valor nuevo, y la salida no debe depender combinacionalmente de start/stop).
        o: metrics_t
        o.cycles = count
        o.running = running
        o.done = running & stop

        if running:
            count += 1
            if stop:
                running = 0
        elif start & ~stop:
            count = 0
            running = 1
        return o

    return cycle_counter, metrics_t


@hw_func
def cycles_byte(cycles: uint64_t, idx: uint3_t) -> uint8_t:
    """Byte `idx` (0 = menos significativo) de `cycles`: orden little-endian, como el resto de
    protocolos del repositorio."""
    return (cycles >> (idx * 8)) & 0xFF
