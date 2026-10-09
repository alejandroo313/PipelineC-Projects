# pyright: reportInvalidTypeForm=none
"""Testbench con el simulador nativo de pypeline para uart_echo_top.py.

Desde este directorio (uart/pipelinec/sim):

    source ../../../env.sh
    python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_uart_echo.py --run all 2>&1 | grep -v '^Clock\\|^$'

Envía unos bytes por RX (tras la ventana de arranque del eco) y comprueba que vuelven iguales por TX.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # los diseños (../)

from pypeline import MAIN, initial, final, sim_input, sim_output, sim_finish
STARTUP_DELAY_S = 0.0005           # ventana de arranque acortada (13 500 ciclos en vez de 1,35 M)
os.environ["UART_STARTUP_DELAY_S"] = str(STARTUP_DELAY_S)
import uart_echo_top as dut

CYC_PER_BIT = int(dut.CLK_FREQ_MHZ * 1_000_000 / dut.BAUD_RATE)
DATA = [0x48, 0x00, 0xFF, 0xA5]    # 'H', 0x00, 0xFF y un patron alterno
BOOT_CYCLES = int(1.5 * STARTUP_DELAY_S * dut.CLK_FREQ_MHZ * 1_000_000)   # algo mas que la ventana de arranque


def build_input():
    return list(DATA)


def build_expected():
    return list(DATA)


# Estado de la simulacion. Se muta SIEMPRE en sitio (dict/list): los cuerpos de
# @sim_input/@sim_output corren sobre una copia de los globales del modulo.
_st = {"cycle": 0, "fsm": 0, "cnt": 0, "nbit": 0, "cur": 0, "rx": [],
       "tx_bytes": [], "expected": [], "line": [], "max_cycles": 0}


def _hx(v):
    return " ".join(f"{b:02X}" for b in v)


@initial(sim=True)
def preparar():
    tx = build_input()
    line = [1] * BOOT_CYCLES
    for b in tx:
        bits = [0] + [(b >> i) & 1 for i in range(8)] + [1]     # start, 8 datos LSB primero, stop
        for bit in bits:
            line += [bit] * CYC_PER_BIT
        line += [1] * (2 * CYC_PER_BIT)                           # hueco entre bytes
    _st["tx_bytes"], _st["expected"], _st["line"] = tx, build_expected(), line
    _st["max_cycles"] = len(line) + 40 * CYC_PER_BIT            # margen para responder
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
    print(("OK: el eco coincide" if ok else "FALLO: el eco no coincide")
          + f" ({s['cycle']} ciclos simulados)")


@MAIN(dut.CLK_FREQ_MHZ)
def tb_driver():
    drive_rx()


@MAIN(dut.CLK_FREQ_MHZ)
def tb_monitor():
    monitor()
