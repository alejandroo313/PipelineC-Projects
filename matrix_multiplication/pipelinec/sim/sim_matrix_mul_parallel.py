# pyright: reportInvalidTypeForm=none
"""Testbench con el simulador nativo de pypeline para matrix_multiplication_parallel.py.

Desde este directorio (matrix_multiplication/pipelinec/sim) (K se fija con NUM_PRODUCTS; BATCHES es el numero de lotes seguidos):

    source ../../../env.sh
    NUM_PRODUCTS=4 BATCHES=2 python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_matrix_mul_parallel.py --run all 2>&1 | grep -v '^Clock\\|^$'

Manda BATCHES lotes seguidos de K pares (A_i, B_i), comprueba que la FPGA devuelve C_i = A_i * B_i de
cada uno y NADA mas, y MIDE donde se va el tiempo:

  entrada      : ciclos que tarda la UART en entregar los K pares
  hueco        : ciclos desde que la FPGA RECIBE el ultimo byte de entrada (la mitad de su bit de stop,
                 que es cuando el receptor lo da por bueno) hasta el INICIO del primer byte de salida.
                 Aqui caben el calculo de las K unidades en paralelo (9 ciclos), el paso a SEND y el
                 arranque del TX
  salida       : ciclos que tarda la UART en devolver las K matrices C
  total / prod.: ciclos del lote entero y por producto

  @initial(sim=True) preparar : construye las tramas y lo esperado, ANTES del primer ciclo
  @sim_input         drive_rx : pone un nivel en rx_pin cada ciclo (trama 8N1)
  @sim_output        monitor  : lee tx_pin cada ciclo, decodifica bytes y toma los tiempos
  @final(sim=True)   informe  : resultado y medidas DESPUES del ultimo ciclo
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # los diseños (../)

from pypeline import MAIN, initial, final, sim_input, sim_output, sim_finish
import matrix_multiplication_parallel as dut      # el diseño; sus Input/Output se leen como dut.<pin>

N = dut.MATRIX_SIZE
K = dut.NUM_PRODUCTS
BATCHES = int(os.environ.get("BATCHES", "2"))
SEED = int(os.environ.get("SEED", "1234"))     # semilla fija: misma prueba en cada ejecucion
CYC_PER_BIT = int(dut.CLK_FREQ_MHZ * 1_000_000 / dut.BAUD_RATE)      # mismo calculo que uart_common.clk_per_bit
SILENCE_BYTES = 30          # tras la ultima respuesta, bytes de silencio que se vigilan (detecta bytes de mas)
OUT_PER_BATCH = K * N * N * 2       # bytes de respuesta de un lote
random.seed(SEED)


# -------------------------------- PROTOCOLO ----------------------------------
def element_bytes(value):
    return [value & 0xFF, value >> 8]               # uint16, byte bajo primero


def build_batch():
    """Devuelve (bytes que se mandan, bytes que debe devolver) para un lote de K pares."""
    tx, rx = [], []
    pairs = []
    for _ in range(K):
        A = [[random.randint(0, 65535) for _ in range(N)] for _ in range(N)]
        B = [[random.randint(0, 65535) for _ in range(N)] for _ in range(N)]
        pairs.append((A, B))
        for M in (A, B):
            for i in range(N):
                for j in range(N):
                    tx += element_bytes(M[i][j])
    for A, B in pairs:
        for i in range(N):
            for k in range(N):
                rx += element_bytes(sum(A[i][j] * B[j][k] for j in range(N)) & 0xFFFF)
    return tx, rx


# Estado de la simulacion. Se muta SIEMPRE en sitio (dict/list): los cuerpos de
# @sim_input/@sim_output corren sobre una copia de los globales del modulo.
_st = {"cycle": 0, "fsm": 0, "cnt": 0, "nbit": 0, "cur": 0, "rx": [], "done_at": None,
       "tx_bytes": [], "expected": [], "line": [], "max_cycles": 0,
       "in_start": [], "in_end": [], "out_start": [], "out_end": []}


def _hx(v):
    return " ".join(f"{b:02X}" for b in v)


@initial(sim=True)
def preparar():
    line = [1] * (2 * CYC_PER_BIT)
    tx_all, expected = [], []
    for _ in range(BATCHES):
        tx, rx = build_batch()
        _st["in_start"].append(len(line))
        for b in tx:
            bits = [0] + [(b >> i) & 1 for i in range(8)] + [1]     # start, 8 datos LSB primero, stop
            for bit in bits:
                line += [bit] * CYC_PER_BIT
            _st["last_byte_end"] = len(line)
            line += [1] * CYC_PER_BIT                               # 1 bit de hueco entre bytes
        _st["in_end"].append(_st["last_byte_end"])                 # fin del ultimo byte de entrada del lote
        # Espera a que la FPGA calcule y devuelva la respuesta antes del siguiente lote
        line += [1] * ((len(rx) + 4) * 10 * CYC_PER_BIT + 200 * CYC_PER_BIT)
        tx_all += tx
        expected += rx
    _st["tx_bytes"], _st["expected"], _st["line"] = tx_all, expected, line
    _st["max_cycles"] = len(line) + 400 * CYC_PER_BIT
    print(f"[TB] {CYC_PER_BIT} ciclos/bit (reloj {dut.CLK_FREQ_MHZ} MHz, {dut.BAUD_RATE} baudios), "
          f"K={K} productos en paralelo, {BATCHES} lotes, semilla={SEED}")
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
            if len(s["rx"]) % OUT_PER_BATCH == 0:               # primer byte de un lote
                s["out_start"].append(s["cycle"])
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
            if len(s["rx"]) % OUT_PER_BATCH == 0:               # ultimo byte de un lote
                s["out_end"].append(s["cycle"])
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
    ok = s["rx"] == s["expected"]
    print(f"enviado  ({len(s['tx_bytes'])} bytes)")
    print(f"esperado ({len(s['expected'])} bytes)")
    print(f"recibido ({len(s['rx'])} bytes)")
    n = min(len(s["in_start"]), len(s["out_start"]), len(s["out_end"]))
    for b in range(n):
        entrada = s["in_end"][b] - s["in_start"][b]
        hueco = s["out_start"][b] - (s["in_end"][b] - CYC_PER_BIT // 2)
        salida = s["out_end"][b] - s["out_start"][b]
        total = s["out_end"][b] - s["in_start"][b]
        print(f"[MEDIDA] lote {b}: K={K} | entrada={entrada} | hueco(calculo)={hueco} | salida={salida} "
              f"| total={total} ciclos | por producto={total / K:.0f} ciclos | hueco/total={100 * hueco / total:.3f} %")
    print(("OK: C_i = A_i*B_i coincide en todos los lotes" if ok else "FALLO: la salida no coincide")
          + f" ({s['cycle']} ciclos simulados)")


@MAIN(dut.CLK_FREQ_MHZ)
def tb_driver():
    drive_rx()


@MAIN(dut.CLK_FREQ_MHZ)
def tb_monitor():
    monitor()
