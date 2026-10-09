#!/usr/bin/env bash
# Sintesis + place & route con Gowin EDA (gw_sh) de la version Verilog, mismo flujo que pypelinec:
# dispositivo GW2AR-LV18QN88C8/I7 (Tang Nano 20K), reloj de 27 MHz.
#
#   ./build_gowin.sh <matrix_mul|matrix_mul_opt> [N=3]
#
# Salida en build/<diseño>_N<N>/ ; informes en impl/pnr/top.rpt.txt, impl/pnr/top.tr.html,
# impl/gwsynthesis/top_syn.rpt.html y el bitstream impl/pnr/top.fs
# (cargar con: openFPGALoader -b tangnano20k <ruta>/top.fs).
set -euo pipefail

TOP="${1:?uso: build_gowin.sh <matrix_mul|matrix_mul_opt> [N]}"
N="${2:-3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/build/${TOP}_N${N}"
source "$HERE/../../env.sh"                   # PATH con gw_sh (~/.local/bin)

rm -rf "$OUT"; mkdir -p "$OUT/src"
UART_V="$HERE/../../uart/verilog"             # la UART Verilog es la del proyecto uart
cp "$UART_V/uart_rx.v" "$UART_V/uart_tx.v" "$HERE/$TOP.v" "$OUT/src/"
# N se fija editando el valor por defecto del parametro en la copia (gw_sh no lo recibe por linea de comandos)
sed -i -E "s/(parameter MATRIX_SIZE *= *)[0-9]+/\1$N/" "$OUT/src/$TOP.v"

cat > "$OUT/build.tcl" <<TCL
set_device --device_version C GW2AR-LV18QN88C8/I7
add_file -type verilog $OUT/src/uart_rx.v
add_file -type verilog $OUT/src/uart_tx.v
add_file -type verilog $OUT/src/$TOP.v
add_file -type cst $HERE/matrix_mul.cst
add_file -type sdc $HERE/matrix_mul.sdc
set_option -output_base_name top
set_option -top_module $TOP
set_option -verilog_std v2001
set_option -gen_text_timing_rpt 1
run all
TCL

echo "[gw_sh] $TOP (N=$N) -> $OUT"
( cd "$OUT" && gw_sh build.tcl ) > "$OUT/gw_sh.log" 2>&1 || { tail -30 "$OUT/gw_sh.log"; exit 1; }
grep -E "^ *(ERROR|Error)" "$OUT/gw_sh.log" | head -5 || true
RPT="$OUT/impl/pnr/top.rpt.txt"
[ -f "$RPT" ] || { echo "No se genero $RPT; mira $OUT/gw_sh.log"; exit 1; }
sed -n '/3. Resource Usage Summary/,/4. I\/O Bank/p' "$RPT" | head -30
echo "Informes: $RPT"
