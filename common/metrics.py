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

Nota de sintesis (Gowin EDA, gw_sh 1.9.x): si el pulso `stop` nace de comparaciones combinacionales de
registros que ya usa la FSM (p. ej. cell.row/cell.col en el matmul), gw_sh aborta con
"giaStr.c: Str_ObjCreate: Assertion `p->nObjs < p->nObjsAlloc' failed" aunque el contador sea
pequeno. Funciona alimentandolo con un registro que la FSM pone a 1 en el ciclo del evento (el pulso
llega un ciclo tarde; si start y stop se retrasan igual, la medida no cambia). Ver
matrix_multiplication_optimized.py (compute_start_r / compute_stop_r).
"""
from pypeline import *


_COUNT_TYPES = {16: uint16_t, 32: uint32_t, 64: uint64_t}


# Un unico tipo para todos los anchos: asi se pueden mezclar contadores de distinto ancho en un diseno.
@struct
class metrics_t(NamedTuple):
    cycles: uint64_t
    running: uint1_t
    done: uint1_t


def make_cycle_counter(bits: int = 64):
    """Crea un contador de `bits` bits (16, 32 o 64). La salida `cycles` es siempre uint64_t.

    Elige el ancho mas pequeno que cubra tu medicion (2^bits ciclos): 16 bits = 2,4 ms a 27 MHz,
    32 bits = 159 s, 64 bits = sin limite practico (por defecto, como los 8 bytes del protocolo)."""
    count_t = _COUNT_TYPES[bits]

    @hw_func
    def cycle_counter(start: uint1_t, stop: uint1_t) -> metrics_t:
        count: Reg[count_t] = 0
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
    # Mux de desplazamientos constantes: el desplazamiento variable de 64 bits (cycles >> idx*8) se
    # simula bien pero PipelineC no lo traduce a VHDL (EX4923 en Gowin).
    rv: uint8_t = cycles & 0xFF
    if idx == 1:
        rv = (cycles >> 8) & 0xFF
    elif idx == 2:
        rv = (cycles >> 16) & 0xFF
    elif idx == 3:
        rv = (cycles >> 24) & 0xFF
    elif idx == 4:
        rv = (cycles >> 32) & 0xFF
    elif idx == 5:
        rv = (cycles >> 40) & 0xFF
    elif idx == 6:
        rv = (cycles >> 48) & 0xFF
    elif idx == 7:
        rv = (cycles >> 56) & 0xFF
    return rv
