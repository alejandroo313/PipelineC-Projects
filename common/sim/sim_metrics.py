# pyright: reportInvalidTypeForm=none
"""Testbench (simulador nativo de pypeline) del contador de ciclos de ../metrics.py.

Desde este directorio (common/sim):

    source ../../env.sh
    python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_metrics.py --run all 2>&1 | grep -v '^Clock\\|^$'

Comprueba la semantica documentada en metrics.py: cycles = t_stop - t_start, start ignorado
mientras cuenta, stop ignorado en reposo, start+stop simultaneos en reposo ignorados, valor
congelado tras stop y serializacion little-endian con cycles_byte.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pypeline import MAIN, initial, final, sim_input, sim_output, sim_finish
import metrics_dut as dut

# (ciclo, start, stop). El resto de ciclos start=stop=0.
EVENTS = [
    (2, 1, 0), (7, 0, 1),                      # medicion A: 5 ciclos
    (10, 1, 0), (11, 0, 1),                    # medicion B: 1 ciclo
    (15, 1, 0), (16, 1, 0), (20, 0, 1),        # C: 20-15 = 5 (segundo start ignorado)
    (22, 0, 1),                                # stop en reposo: ignorado
    (25, 1, 1),                                # start+stop simultaneos en reposo: ignorados
    (30, 1, 0), (130, 0, 1),                   # D: 100 ciclos
    (200, 1, 0), (556, 0, 1),                  # E: 356 = 0x0164 (comprueba el byte 1)
]
EXPECTED_DONE = [5, 1, 5, 100, 356]            # valor definitivo de cada pulso `done`
IDX_START = 600                                # a partir de aqui se recorre idx 0..7
END = IDX_START + 8 * 3 + 5

_st = {"cycle": 0, "done_cycles": [], "cyc": [], "bytes": {}}


def _sched():
    return {c: (s, p) for c, s, p in EVENTS}


@initial(sim=True)
def preparar():
    print(f"[TB] {len(EVENTS)} eventos, {len(EXPECTED_DONE)} mediciones esperadas")


@sim_input
def drive():
    c = _st["cycle"]
    s, p = _sched().get(c, (0, 0))
    dut.start_in = s
    dut.stop_in = p
    dut.idx_in = ((c - IDX_START) // 3) % 8 if c >= IDX_START else 0


@sim_output
def monitor():
    s = _st
    c = s["cycle"]
    s["cyc"].append(int(dut.cycles_out))
    if int(dut.done_out):
        s["done_cycles"].append(c)
    if c >= IDX_START:
        idx = ((c - IDX_START) // 3) % 8
        s["bytes"].setdefault(idx, int(dut.byte_out))
    s["cycle"] += 1
    if s["cycle"] >= END:
        sim_finish()


@final(sim=True)
def informe():
    s = _st
    ok = True
    # 1) un pulso done por medicion, en el ciclo del stop (el sim puede introducir latencia fija L)
    stops = [c for c, st, sp in EVENTS if sp and c in (7, 11, 20, 130, 556)]
    got = s["done_cycles"]
    print(f"done en ciclos {got}; stops validos en {stops}")
    if len(got) != len(stops):
        print("FALLO: numero de pulsos done distinto del esperado")
        ok = False
    else:
        lat = {g - e for g, e in zip(got, stops)}
        if len(lat) != 1:
            print(f"FALLO: latencia entre stop y done no constante: {lat}")
            ok = False
        # 2) valor definitivo = cycles un ciclo despues del pulso done
        vals = [s["cyc"][g + 1] for g in got]
        print(f"ciclos medidos {vals}; esperados {EXPECTED_DONE}")
        ok &= vals == EXPECTED_DONE
        # 3) valor congelado hasta el siguiente start valido
        frozen = s["cyc"][got[-1] + 1: IDX_START]
        if any(v != EXPECTED_DONE[-1] for v in frozen):
            print("FALLO: el valor no se mantiene congelado tras stop")
            ok = False
    # 4) serializacion little-endian de 356 = 0x0164
    b = [s["bytes"].get(i) for i in range(8)]
    print(f"bytes idx 0..7: {b} (esperado [100, 1, 0, 0, 0, 0, 0, 0])")
    ok &= b == [0x64, 0x01, 0, 0, 0, 0, 0, 0]
    print("OK: el contador de ciclos cumple la semantica" if ok else "FALLO: ver mensajes anteriores")


@MAIN(dut.CLK_FREQ_MHZ)
def tb_driver():
    drive()


@MAIN(dut.CLK_FREQ_MHZ)
def tb_monitor():
    monitor()
