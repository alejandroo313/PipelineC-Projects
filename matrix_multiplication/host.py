#!/usr/bin/env python3
"""Host del matmul (matmul/optimized): envia A y B, lee C + 2 contadores de ciclos, verifica en CPU y
anade una fila a results/results.csv por ejecucion.

Lo lanza `run.py <proyecto> --bench`; tambien se puede usar a mano:
    python3 host.py --port /dev/ttyUSB1 --runs 5 --csv ../results/results.csv
"""
import argparse
import importlib.util
import random
import sys
from pathlib import Path

import numpy as np
import serial

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
import hostlib

# El cliente existente (app/matrix_multiplication.py) pone el protocolo; aqui solo se reutiliza.
_spec = importlib.util.spec_from_file_location("mm_client", HERE / "app" / "matrix_multiplication.py")
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", default=mm.DEFAULT_PORT)
    p.add_argument("--baud", type=int, default=mm.DEFAULT_BAUD)
    p.add_argument("--project", default="matmul/optimized")
    p.add_argument("--board", default="tangnano20k")
    p.add_argument("--clk-mhz", type=float, default=27.0)
    p.add_argument("--fmax", type=float, default=None, help="fmax de la sintesis (MHz), lo pasa run.py")
    p.add_argument("--git", default="")
    p.add_argument("--csv", default=str(HERE.parent / "results" / "results.csv"))
    p.add_argument("-n", type=int, default=3)
    p.add_argument("--runs", type=int, default=5, help="ejecuciones (una fila de CSV cada una)")
    p.add_argument("--seed", type=int, default=None)
    a = p.parse_args()
    if a.seed is not None:
        random.seed(a.seed)

    failed = 0
    with serial.Serial(a.port, a.baud, timeout=1) as ser:
        for i in range(a.runs):
            A, B = mm.random_matrix(a.n), mm.random_matrix(a.n)
            C, total, compute = mm.matrix_multiply(ser, A, B, metricas=True)
            expected = mm.expected_product(A, B)
            ok = C == expected

            An, Bn = np.array(A, dtype=np.uint16), np.array(B, dtype=np.uint16)
            assert (An @ Bn).tolist() == expected, "numpy no coincide con la referencia"   # uint16 envuelve mod 2^16
            t_cpu = hostlib.time_cpu_us(lambda: An @ Bn)

            row = hostlib.make_row(a.project, a.board, a.n, total, compute, a.clk_mhz, a.fmax, t_cpu, ok, a.git)
            hostlib.append_row(a.csv, row)
            print(f"[{i + 1}/{a.runs}] {'OK ' if ok else 'ERROR'} total={total} ciclos ({row['tiempo_fpga_us']} us) "
                  f"compute={compute} ({row['tiempo_compute_us']} us) cpu={row['tiempo_cpu_us']} us "
                  f"speedup={row['speedup']} (solo calculo: {row['speedup_compute']})")
            failed += not ok
    print(f"-> {a.csv}")
    if failed:
        raise SystemExit(f"{failed} de {a.runs} ejecuciones con resultado incorrecto")


if __name__ == "__main__":
    main()
