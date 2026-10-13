#!/usr/bin/env python3
"""Graficos a partir de results/results.csv (solo filas con ok=1).

    python3 common/plot_results.py [--csv results/results.csv] [--out results/plots]

Genera:
  tiempos.png      tiempo FPGA (total y solo calculo) frente a CPU por proyecto (mediana, escala log)
  speedup_n.png    speedup frente a N por proyecto (total con UART y solo calculo); la linea y=1 es la CPU
"""
import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostlib

COLORS = {"total": "#4C78A8", "compute": "#54A24B", "cpu": "#B279A2"}


def _med(rows, key):
    return statistics.median(float(r[key]) for r in rows)


def plot_tiempos(groups, out):
    names = sorted(groups)
    series = [("FPGA total (con UART)", "tiempo_fpga_us", COLORS["total"]),
              ("FPGA solo calculo", "tiempo_compute_us", COLORS["compute"]),
              ("CPU (NumPy)", "tiempo_cpu_us", COLORS["cpu"])]
    fig, ax = plt.subplots(figsize=(max(5, 2.2 * len(names) + 2), 4))
    w = 0.8 / len(series)
    for k, (label, key, color) in enumerate(series):
        xs = [i + (k - 1) * w for i in range(len(names))]
        ys = [_med(groups[nm], key) for nm in names]
        bars = ax.bar(xs, ys, w, label=label, color=color)
        for b, y in zip(bars, ys):
            ax.annotate(f"{y:.3g}", (b.get_x() + b.get_width() / 2, y), ha="center", va="bottom", fontsize=8)
    ax.set_yscale("log")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([f"{nm}\n(n={len(groups[nm])} ejec.)" for nm in names])
    ax.set_ylabel("tiempo por producto (us, escala log)")
    ax.set_title("Tiempo por multiplicacion: FPGA frente a CPU")
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_speedup(rows, out):
    by_proj = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_proj[r["proyecto"]][int(r["n"])].append(r)
    fig, ax = plt.subplots(figsize=(6, 4))
    for i, proj in enumerate(sorted(by_proj)):
        ns = sorted(by_proj[proj])
        for key, ls, lab in (("speedup", "-", "total"), ("speedup_compute", "--", "solo calculo")):
            ax.plot(ns, [_med(by_proj[proj][n], key) for n in ns], ls, marker="o",
                    color=list(COLORS.values())[i % 3 if key == "speedup" else (i + 1) % 3],
                    label=f"{proj} ({lab})")
    ax.axhline(1, color="grey", lw=0.8, ls=":")
    ax.set_yscale("log")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.margins(x=0.25, y=0.3)
    ax.set_xlabel("N (matriz N x N)")
    ax.set_ylabel("speedup = t_CPU / t_FPGA (>1: gana la FPGA)")
    ax.set_title("Speedup frente a CPU")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def main():
    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--csv", default=str(root / "results" / "results.csv"))
    p.add_argument("--out", default=str(root / "results" / "plots"))
    a = p.parse_args()
    if not Path(a.csv).exists():
        sys.exit(f"no existe {a.csv}: ejecuta `python run.py <proyecto> --bench` primero")
    rows = [r for r in hostlib.read_rows(a.csv) if r["ok"] == "1"]
    if not rows:
        sys.exit("el CSV no tiene filas con ok=1")
    groups = defaultdict(list)
    for r in rows:
        groups[r["proyecto"]].append(r)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    plot_tiempos(groups, out / "tiempos.png")
    plot_speedup(rows, out / "speedup_n.png")
    print(f"{len(rows)} filas -> {out}/tiempos.png, {out}/speedup_n.png")


if __name__ == "__main__":
    main()
