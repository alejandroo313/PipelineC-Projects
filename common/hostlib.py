"""Utilidades del lado PC para medir un diseno FPGA: trama `resultado + ciclos`, tiempo en CPU y CSV.

Independiente del proyecto. Un host.py concreto (p. ej. matrix_multiplication/host.py) solo pone el
protocolo de su programa y llama a estas funciones.

Trama de respuesta (ver common/metrics.py): bytes de resultado seguidos de uno o mas contadores de
ciclos uint64 little-endian (8 bytes cada uno). Tiempo real = ciclos / clk_mhz (reloj de la FPGA,
NO fmax).
"""
import csv
import statistics
import struct
import time
from datetime import datetime
from pathlib import Path

CSV_COLUMNS = [
    "fecha", "proyecto", "placa", "n", "ciclos_total", "ciclos_compute", "clk_mhz", "fmax_mhz",
    "tiempo_fpga_us", "tiempo_compute_us", "tiempo_cpu_us", "speedup", "speedup_compute", "ok", "git_hash",
]


def split_frame(data: bytes, result_len: int, n_counters: int):
    """(resultado, [ciclos...]) a partir de la trama completa."""
    need = result_len + 8 * n_counters
    if len(data) != need:
        raise ValueError(f"trama de {len(data)} bytes, esperados {need}")
    counters = struct.unpack(f"<{n_counters}Q", data[result_len:])
    return data[:result_len], list(counters)


def time_cpu_us(fn, min_batch_s: float = 0.05, batches: int = 9) -> float:
    """Mediana (us por llamada) de `fn()` repetida en lotes de al menos `min_batch_s` segundos.
    Con tareas de microsegundos domina la sobrecarga de la llamada: usalo como cota, no como
    rendimiento asintotico."""
    n = 1
    while True:
        t = time.perf_counter()
        for _ in range(n):
            fn()
        if time.perf_counter() - t >= min_batch_s:
            break
        n *= 2
    samples = []
    for _ in range(batches):
        t = time.perf_counter()
        for _ in range(n):
            fn()
        samples.append((time.perf_counter() - t) / n * 1e6)
    return statistics.median(samples)


def make_row(proyecto, placa, n, ciclos_total, ciclos_compute, clk_mhz, fmax_mhz, tiempo_cpu_us, ok, git_hash):
    t_total = ciclos_total / clk_mhz
    t_comp = ciclos_compute / clk_mhz
    return {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "proyecto": proyecto, "placa": placa, "n": n,
        "ciclos_total": ciclos_total, "ciclos_compute": ciclos_compute,
        "clk_mhz": clk_mhz, "fmax_mhz": fmax_mhz if fmax_mhz is not None else "",
        "tiempo_fpga_us": f"{t_total:.3f}", "tiempo_compute_us": f"{t_comp:.3f}",
        "tiempo_cpu_us": f"{tiempo_cpu_us:.3f}",
        "speedup": f"{tiempo_cpu_us / t_total:.6f}" if t_total else "",
        "speedup_compute": f"{tiempo_cpu_us / t_comp:.4f}" if t_comp else "",
        "ok": int(bool(ok)), "git_hash": git_hash,
    }


def append_row(path, row):
    """Anade una fila; crea el fichero con cabecera si no existe. Si la cabecera existente no
    coincide con CSV_COLUMNS aborta (no mezcla formatos)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    if not new:
        header = path.read_text().splitlines()[0].split(",")
        if header != CSV_COLUMNS:
            raise SystemExit(f"{path}: cabecera distinta de la esperada; renombra el fichero antiguo")
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)


def read_rows(path):
    with Path(path).open(newline="") as f:
        return list(csv.DictReader(f))
