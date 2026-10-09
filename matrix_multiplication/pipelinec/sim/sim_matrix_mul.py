# pyright: reportInvalidTypeForm=none
"""Testbench con el simulador nativo de pypeline (sin VHDL, sin Verilator).

Desde este directorio (matrix_multiplication/pipelinec/sim):

    source ../../../env.sh
    python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_matrix_mul.py --run all | grep -v '^Clock\\|^$'

El PC se modela con Python normal y las anotaciones de pypeline:
  @initial(sim=True) preparar : construye la trama y lo esperado, ANTES del primer ciclo
  @sim_input         drive_rx : pone un nivel en rx_pin cada ciclo (trama 8N1)
  @sim_output        monitor  : lee tx_pin cada ciclo, decodifica bytes; sim_finish() al terminar
  @final(sim=True)   informe  : imprime el resultado DESPUES del ultimo ciclo
                                (tambien si la simulacion acaba por --run N o por un error)
Los sim_print de matrix_multiplication.py salen intercalados en la misma consola.
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # los diseños (../)

from pypeline import MAIN, initial, final, sim_input, sim_output, sim_finish
import matrix_multiplication_optimized as dut            # el diseño; sus Input/Output se leen como dut.<pin>

N = 3
CYC_PER_BIT = int(dut.CLK_FREQ_MHZ * 1_000_000 / dut.BAUD_RATE)      # mismo calculo que uart_common.clk_per_bit

# TODO: probar DOS tramas seguidas (A1,B1 y luego A2,B2) y comprobar C1 y C2, y que tras C2
#       no llegue ningun byte mas. Una sola trama no detecta ni que el TX repita el ultimo
#       byte al terminar, ni que 'cell' no se reinicie entre tramas. Hace falta: generar
#       varias parejas (A, B) en build_input/build_expected y esperar len(expected) bytes
#       mas un margen de silencio antes de sim_finish().
# ------------------------------ DATOS DE PRUEBA ------------------------------
SEED = 1234        # semilla fija: misma prueba en cada ejecucion (cambiala para probar otros datos)
random.seed(SEED)
A = [[random.randint(0, 65535) for _ in range(N)] for _ in range(N)]
B = [[random.randint(0, 65535) for _ in range(N)] for _ in range(N)]


# -------------------------------- PROTOCOLO ----------------------------------
def build_input():
    """Bytes que se mandan a la FPGA: A y B, fila a fila, 2 bytes/elemento, byte bajo primero."""
    out = []
    for M in (A, B):
        for i in range(N):
            for j in range(N):
                out += [M[i][j] & 0xFF, M[i][j] >> 8]
    return out


def build_expected():
    """Bytes que deberia devolver la FPGA: C = A*B, mismo formato."""
    out = []
    for i in range(N):
        for k in range(N):
            acc = sum(A[i][j] * B[j][k] for j in range(N)) & 0xFFFF
            out += [acc & 0xFF, acc >> 8]
    return out


# Estado de la simulacion. Se muta SIEMPRE en sitio (dict/list): los cuerpos de
# @sim_input/@sim_output corren sobre una copia de los globales del modulo.
_st = {"cycle": 0, "fsm": 0, "cnt": 0, "nbit": 0, "cur": 0, "rx": [],
       "tx_bytes": [], "expected": [], "line": [], "max_cycles": 0}


def _hx(v):
    return " ".join(f"{b:02X}" for b in v)


@initial(sim=True)
def preparar():
    tx = build_input()
    line = [1] * (2 * CYC_PER_BIT)
    for b in tx:
        bits = [0] + [(b >> i) & 1 for i in range(8)] + [1]     # start, 8 datos LSB primero, stop
        for bit in bits:
            line += [bit] * CYC_PER_BIT
        line += [1] * CYC_PER_BIT                               # 1 bit de hueco entre bytes
    _st["tx_bytes"], _st["expected"], _st["line"] = tx, build_expected(), line
    _st["max_cycles"] = len(line) + 400 * CYC_PER_BIT           # margen para calcular y responder
    print(f"[TB] {CYC_PER_BIT} ciclos/bit (reloj {dut.CLK_FREQ_MHZ} MHz, {dut.BAUD_RATE} baudios); "
          f"{len(tx)} bytes de entrada, {len(_st['expected'])} esperados")


@sim_input
def drive_rx():
    line, c = _st["line"], _st["cycle"]
    dut.rx_pin = line[c] if c < len(line) else 1


@sim_output
def monitor():
    out = int(dut.tx_pin)
    s = _st
    if s["fsm"] == 0:                                           # idle: espera flanco de start
        if out == 0:
            s["fsm"], s["cnt"] = 1, 0
    elif s["fsm"] == 1:                                         # start: va a mitad de bit
        s["cnt"] += 1
        if s["cnt"] == CYC_PER_BIT // 2:
            s["fsm"], s["cnt"], s["nbit"], s["cur"] = 2, 0, 0, 0
    elif s["fsm"] == 2:                                         # 8 bits de datos
        s["cnt"] += 1
        if s["cnt"] == CYC_PER_BIT:
            s["cnt"] = 0
            s["cur"] |= out << s["nbit"]
            s["nbit"] += 1
            if s["nbit"] == 8:
                s["fsm"] = 3
    else:                                                       # stop
        s["cnt"] += 1
        if s["cnt"] == CYC_PER_BIT:
            s["rx"].append(s["cur"])
            print(f"[PC ] byte {len(s['rx'])} recibido = {s['cur']:02X} (ciclo {s['cycle']})")
            s["fsm"] = 0

    s["cycle"] += 1
    if len(s["rx"]) >= len(s["expected"]) or s["cycle"] >= s["max_cycles"]:
        sim_finish()


@final(sim=True)
def informe():
    s = _st
    print(f"enviado  ({len(s['tx_bytes'])} bytes): {_hx(s['tx_bytes'])}")
    print(f"esperado ({len(s['expected'])} bytes): {_hx(s['expected'])}")
    print(f"recibido ({len(s['rx'])} bytes): {_hx(s['rx'])}")
    ok = s["rx"] == s["expected"]
    print(("OK: C = A*B coincide" if ok else "FALLO: la salida no coincide")
          + f" ({s['cycle']} ciclos simulados)")


@MAIN(dut.CLK_FREQ_MHZ)
def tb_driver():
    drive_rx()


@MAIN(dut.CLK_FREQ_MHZ)
def tb_monitor():
    monitor()
