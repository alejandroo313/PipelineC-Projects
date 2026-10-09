#!/usr/bin/env python3
"""Uso: python run.py <proyecto> [--build] [--flash] [--bench]   |   python run.py --list

Flujo real: PipelineC (pypelinec) -> Gowin EDA (gw_sh: síntesis + P&R + bitstream) -> openFPGALoader.
Sin flags de acción hace build + flash + bench. Requiere `source env.sh` (PIPELINEC_DIR, PATH).
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIPELINEC_DIR = Path(os.environ.get("PIPELINEC_DIR", Path.home() / "Documents/tfg/PipelineC"))
PIPELINEC = PIPELINEC_DIR / "src" / "pypelinec"
DEFAULT_BOARD = "tangnano20k"    # nombre de placa de openFPGALoader (`openFPGALoader --list-boards`)


def projects():
    """nombre (campo `name` del toml) -> (directorio, config)"""
    out = {}
    for t in sorted(ROOT.glob("*/project.toml")):
        cfg = tomllib.loads(t.read_text())
        variants = cfg.pop("variants", {})
        if not variants:
            out[cfg["name"]] = (t.parent, cfg)
            continue
        default = cfg.get("default_variant", next(iter(variants)))
        for v, over in variants.items():
            vcfg = {**cfg, **over, "name": f"{cfg['name']}/{v}", "project": cfg["name"]}
            out[vcfg["name"]] = (t.parent, vcfg)
            if v == default:
                out[cfg["name"]] = (t.parent, vcfg)    # alias: `matmul` == `matmul/<default>`
    return out


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def git_hash():
    h = git("rev-parse", "--short", "HEAD")
    return h + ("-dirty" if git("status", "--porcelain", "--untracked-files=no") else "")


def src_hash(d, cfg):
    """Hash de fuentes + pines + parte + versión de PipelineC: si no cambia, no se resintetiza."""
    h = hashlib.sha256()
    for s in cfg["sources"]:
        base = (d / s).resolve()
        for f in sorted(base.rglob("*")):
            if f.suffix in {".py", ".cst"} and "sim" not in f.relative_to(base).parts[:-1]:
                h.update(str(f.relative_to(ROOT.resolve())).encode())
                h.update(f.read_bytes())
    for k in ("top", "pins"):
        h.update((d / cfg[k]).read_bytes())
    h.update(cfg["part"].encode())
    h.update(json.dumps(cfg.get("env", {}), sort_keys=True).encode())
    h.update(subprocess.run(["git", "rev-parse", "HEAD"], cwd=PIPELINEC_DIR,
                            capture_output=True, text=True).stdout.encode())
    return h.hexdigest()[:12]


def parse_reports(pnr):
    """fmax de top.tr y tabla de recursos de top.rpt.txt (formato Gowin EDA)."""
    tr = (pnr / "top.tr").read_text(errors="replace")
    m = re.search(r"Max Frequency Summary.*?\n\s*\d+\s+\S+\s+[\d.]+\(MHz\)\s+([\d.]+)\(MHz\)",
                  tr[tr.rfind("Max Frequency Summary"):], re.S)
    fmax = float(m.group(1)) if m else None
    res = {}
    rpt = (pnr / "top.rpt.txt").read_text(errors="replace")
    sec = rpt.split("3. Resource Usage Summary")[1].split("====")[0]
    for line in sec.splitlines():
        c = [x.strip() for x in line.split("|")]
        if len(c) >= 3 and (m := re.match(r"(\d+)\s*/\s*(\d+)", c[1])):
            res[c[0].lstrip("- ").strip()] = {"used": int(m.group(1)), "total": int(m.group(2))}
    return {"fmax_mhz": fmax, "resources": res}


def build(d, cfg, force):
    h = src_hash(d, cfg)
    bdir = ROOT / "build" / cfg["name"]
    out = bdir / h
    pnr = out / "top" / "impl" / "pnr"
    if (out / "metrics.json").exists() and (pnr / "top.fs").exists() and not force:
        print(f"[build] sin cambios, reutilizo build/{cfg['name']}/{h}")
        return json.loads((out / "metrics.json").read_text())
    if not PIPELINEC.exists():
        sys.exit(f"[build] no existe {PIPELINEC}; haz `source env.sh` o exporta PIPELINEC_DIR")
    tmp = bdir / f"{h}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    bdir.mkdir(parents=True, exist_ok=True)
    cmd = [str(PIPELINEC), str((d / cfg["top"]).resolve()), "--part", cfg["part"],
           "--pins", str((d / cfg["pins"]).resolve()), "--out_dir", str(tmp)]
    print("[build]", " ".join(cmd), "\n[build] el P&R tarda minutos...")
    p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, env={**os.environ, **cfg.get("env", {})})
    (bdir / f"{h}.log").write_text(p.stdout + p.stderr)
    if p.returncode != 0 or not (tmp / "top/impl/pnr/top.fs").exists():
        sys.exit(f"[build] falló (rc={p.returncode}); log en {bdir}/{h}.log")
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    info = {"hash": h, "fs": str(pnr / "top.fs"), **parse_reports(pnr)}
    (out / "metrics.json").write_text(json.dumps(info, indent=2))
    return info


def for_board(cfg, board):
    """La config base es la de DEFAULT_BOARD; otra placa necesita su tabla [boards.<placa>] en el toml."""
    if board == DEFAULT_BOARD:
        return cfg
    ov = cfg.get("boards", {}).get(board)
    if ov is None:
        sys.exit(f"[board] '{cfg['name']}' no tiene [boards.{board}] en su project.toml: "
                 f"añade al menos `part` y `pins` para esa placa")
    return {**cfg, **ov, "board": board}


def flash(info, persist, board):
    cmd = ["openFPGALoader", "-b", board] + (["-f"] if persist else []) + [info["fs"]]
    print("[flash]", " ".join(cmd), "(flash)" if persist else "(SRAM)")
    subprocess.run(cmd, check=True)


def bench(d, cfg, info):
    host = d / cfg["host"]
    if not host.exists():
        sys.exit(f"[bench] no existe {host} (se escribe en el paso 2)")
    (ROOT / "results").mkdir(exist_ok=True)
    cmd = [sys.executable, str(host), "--port", cfg["port"], "--baud", str(cfg["baud"]),
           "--project", cfg["name"], "--board", cfg.get("board", DEFAULT_BOARD), "--clk-mhz", str(cfg["clk_mhz"]),
           "--fmax", str(info["fmax_mhz"]), "--git", git_hash(),
           "--csv", str(ROOT / "results" / "results.csv"), *cfg.get("host_args", [])]
    print("[bench]", " ".join(cmd))
    subprocess.run(cmd, cwd=d, check=True)


def main():
    ap = argparse.ArgumentParser(
        prog="run.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Sintetiza (PipelineC + Gowin EDA), flashea (openFPGALoader) y mide un proyecto.",
        epilog="""ejemplos:
  python run.py --list                    lista los proyectos
  python run.py matmul                    build + flash (SRAM) + bench
  python run.py matmul --build            solo sintetiza (usa caché si las fuentes no cambian)
  python run.py matmul --flash            flashea a SRAM (demo temporal)
  python run.py matmul --flash --persist  flashea a flash (demo final)
  python run.py matmul/parallel --build   construye un subproyecto
  python run.py matmul --build --force    resintetiza ignorando la caché
  python run.py matmul --board tangnano9k construye y flashea para otra placa (ver abajo)

placas (--board):
  Por defecto tangnano20k (Sipeed Tang Nano 20K, GW2AR-LV18QN88C8/I7). El valor es el nombre de
  placa de openFPGALoader (`openFPGALoader --list-boards`) y se usa tal cual en `flash`.
  Para otra placa, el project.toml del proyecto debe tener una tabla [boards.<placa>] que
  sobreescriba lo que cambie (como mínimo `part` y `pins`; también `clk_mhz` si el reloj no
  es de 27 MHz). Sin esa tabla, run.py aborta en vez de usar la parte/pines de la 20K.

subproyectos de matmul (matrix_multiplication/):
  matmul/base       3x3, toda C en un ciclo (18 DSP)
  matmul/optimized  3x3, una celda de C por ciclo (2 DSP); `matmul` es alias de este
  matmul/n          tamaño N variable (1..8), N llega por UART (host: --con-n)
  matmul/parallel   lote de K productos 3x3 a la vez (K=NUM_PRODUCTS en el toml)

salida de las builds: build/<proyecto>/<hash>/ (bitstream: top/impl/pnr/top.fs)
requiere `source env.sh` (PIPELINEC_DIR y PATH de Gowin/openFPGALoader)""")
    ap.add_argument("project", nargs="?", help="nombre del proyecto (ver --list); 'help' muestra esta ayuda")
    ap.add_argument("--list", action="store_true", help="listar proyectos disponibles")
    ap.add_argument("--build", action="store_true", help="sintetizar y generar el .fs")
    ap.add_argument("--flash", action="store_true", help="cargar el .fs en la placa")
    ap.add_argument("--bench", action="store_true", help="ejecutar host.py y añadir fila a results/results.csv")
    ap.add_argument("--board", default=DEFAULT_BOARD, metavar="PLACA",
                    help=f"placa destino, nombre de openFPGALoader (por defecto {DEFAULT_BOARD})")
    ap.add_argument("--persist", action="store_true", help="con --flash: grabar en flash (por defecto SRAM)")
    ap.add_argument("--force", action="store_true", help="ignorar la caché de síntesis")
    a = ap.parse_args()
    if a.project == "help":
        ap.print_help()
        return
    ps = projects()
    if a.list or not a.project:
        for n, (d, c) in ps.items():
            alias = "  (alias de " + c["name"] + ")" if n != c["name"] else ""
            print(f"{n}\t({d.name}/){alias}")
        return
    if a.project not in ps:
        sys.exit(f"proyecto '{a.project}' desconocido; disponibles: {', '.join(ps)}")
    d, cfg = ps[a.project]
    cfg = for_board(cfg, a.board)
    if not (a.build or a.flash or a.bench):
        a.build = a.flash = a.bench = True
    info = build(d, cfg, a.force)
    print(f"[build] fmax={info['fmax_mhz']} MHz (reloj real {cfg['clk_mhz']} MHz) "
          f"LUT={info['resources'].get('LUT,ALU,ROM16', info['resources'].get('Logic'))}")
    if a.flash:
        flash(info, a.persist, a.board)
    if a.bench:
        bench(d, cfg, info)


if __name__ == "__main__":
    main()
