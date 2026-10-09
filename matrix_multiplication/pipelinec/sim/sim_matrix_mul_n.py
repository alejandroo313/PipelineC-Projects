# pyright: reportInvalidTypeForm=none
"""Testbench con el simulador nativo de pypeline para matrix_multiplication_n.py.

Desde este directorio (matrix_multiplication/pipelinec/sim):

    source ../../../env.sh
    python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_matrix_mul_n.py --run all 2>&1 | grep -v '^Clock\\|^$'

Manda VARIAS tramas seguidas, cada una con su tamaño N (primer byte), y comprueba que la FPGA
devuelve C = A*B de cada una y NADA mas. Mezclar tamaños prueba que la FSM se reinicia bien
entre tramas. Se pueden cambiar sin tocar el fichero:

    MATRIX_SIZES=3,2,4 SEED=7 python3 ... sim_matrix_mul_n.py --run all

Tambien se puede probar un tamaño no valido (p. ej. 0 o 9): la FPGA debe ignorar ese byte
y atender la siguiente trama valida (se espera NINGUNA respuesta para el).

  @initial(sim=True) preparar : construye las tramas y lo esperado, ANTES del primer ciclo
  @sim_input         drive_rx : pone un nivel en rx_pin cada ciclo (trama 8N1)
  @sim_output        monitor  : lee tx_pin cada ciclo y decodifica los bytes
  @final(sim=True)   informe  : imprime el resultado DESPUES del ultimo ciclo
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # los diseños (../)

from pypeline import MAIN, initial, final, sim_input, sim_output, sim_finish
import matrix_multiplication_n as dut       # el diseño; sus Input/Output se leen como dut.<pin>

CYC_PER_BIT = int(dut.CLK_FREQ_MHZ * 1_000_000 / dut.BAUD_RATE)     # mismo calculo que uart_common.clk_per_bit
SILENCE_BYTES = 30          # tras la ultima respuesta, bytes de silencio que se vigilan (detecta bytes de mas)

# ------------------------------ DATOS DE PRUEBA ------------------------------
SIZES = [int(x) for x in os.environ.get("MATRIX_SIZES", "3,2,4").split(",")]
SEED = int(os.environ.get("SEED", "1234"))      # semilla fija: misma prueba en cada ejecucion
random.seed(SEED)


# -------------------------------- PROTOCOLO ----------------------------------
def element_bytes(value):
    return [value & 0xFF, value >> 8]               # uint16, byte bajo primero


def build_frame(n):
    """Devuelve (bytes que se mandan a la FPGA, bytes que deberia devolver) para una matriz NxN.
    Un n fuera de 1..MAX_N se manda tal cual (sin matrices) y no espera respuesta."""
    if not 1 <= n <= dut.MAX_N:
        return [n], []
    A = [[random.randint(0, 65535) for _ in range(n)] for _ in range(n)]
    B = [[random.randint(0, 65535) for _ in range(n)] for _ in range(n)]
    tx = [n]
    for M in (A, B):
        for i in range(n):
            for j in range(n):
                tx += element_bytes(M[i][j])
    rx = []
    for i in range(n):
        for k in range(n):
            rx += element_bytes(sum(A[i][j] * B[j][k] for j in range(n)) & 0xFFFF)
    return tx, rx


# Estado de la simulacion. Se muta SIEMPRE en sitio (dict/list): los cuerpos de
# @sim_input/@sim_output corren sobre una copia de los globales del modulo.
_st = {"cycle": 0, "fsm": 0, "cnt": 0, "nbit": 0, "cur": 0, "rx": [], "done_at": None,
       "tx_bytes": [], "expected": [], "line": [], "max_cycles": 0}


def _hx(v):
    return " ".join(f"{b:02X}" for b in v)


@initial(sim=True)
def preparar():
    line = [1] * (2 * CYC_PER_BIT)
    tx_all, expected = [], []
    for n in SIZES:
        tx, rx = build_frame(n)
        for b in tx:
            bits = [0] + [(b >> i) & 1 for i in range(8)] + [1]     # start, 8 datos LSB primero, stop
            for bit in bits:
                line += [bit] * CYC_PER_BIT
            line += [1] * CYC_PER_BIT                               # 1 bit de hueco entre bytes
        # Espera a que la FPGA calcule y devuelva la respuesta antes de la siguiente trama
        line += [1] * ((len(rx) + 4) * 10 * CYC_PER_BIT + 200 * CYC_PER_BIT)
        tx_all += tx
        expected += rx
    _st["tx_bytes"], _st["expected"], _st["line"] = tx_all, expected, line
    _st["max_cycles"] = len(line) + 400 * CYC_PER_BIT
    print(f"[TB] {CYC_PER_BIT} ciclos/bit (reloj {dut.CLK_FREQ_MHZ} MHz, {dut.BAUD_RATE} baudios), "
          f"MAX_N={dut.MAX_N}, tamaños={SIZES}, semilla={SEED}")
    print(f"[TB] {len(tx_all)} bytes de entrada, {len(expected)} esperados")


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
    # Se acaba cuando ya esta todo recibido Y ha pasado un rato de silencio (para pillar bytes de mas)
    if s["done_at"] is None and len(s["rx"]) >= len(s["expected"]) and s["cycle"] >= len(s["line"]):
        s["done_at"] = s["cycle"]
    if s["done_at"] is not None and s["cycle"] - s["done_at"] > SILENCE_BYTES * 10 * CYC_PER_BIT:
        sim_finish()
    if s["cycle"] >= s["max_cycles"]:
        sim_finish()


@final(sim=True)
def informe():
    s = _st
    print(f"enviado  ({len(s['tx_bytes'])} bytes): {_hx(s['tx_bytes'])}")
    print(f"esperado ({len(s['expected'])} bytes): {_hx(s['expected'])}")
    print(f"recibido ({len(s['rx'])} bytes): {_hx(s['rx'])}")
    ok = s["rx"] == s["expected"]
    print(("OK: C = A*B coincide en todas las tramas" if ok else "FALLO: la salida no coincide")
          + f" ({s['cycle']} ciclos simulados)")


@MAIN(dut.CLK_FREQ_MHZ)
def tb_driver():
    drive_rx()


@MAIN(dut.CLK_FREQ_MHZ)
def tb_monitor():
    monitor()
