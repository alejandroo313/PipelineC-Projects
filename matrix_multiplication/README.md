# UART Matrix Multiplication

FPGA accelerator (Sipeed Tang Nano 20K, 27 MHz clock) that receives two `uint16` matrices over UART, computes `C = A · B` and sends C back over the same channel. It is written in PipelineC and compared against a hand-written Verilog version. The UART is reused from [`../uart/pipelinec/`](../uart/pipelinec/).

**Protocol** (8N1, 1 Mbaud): `uint16` elements in little-endian, row by row.

```
PC -> FPGA : A (N·N·2 bytes) + B (N·N·2 bytes)      [the variable-size version prepends 1 byte with N]
FPGA -> PC : C = A·B (N·N·2 bytes)                  [arithmetic modulo 2^16]
```

## Contents

| Path | Description |
|------|-------------|
| `pipelinec/matrix_multiplication.py` | Base 3×3 version: computes all of C in one cycle (27 parallel products). |
| `pipelinec/matrix_multiplication_optimized.py` | Optimized 3×3: one element of C per cycle, to save DSP blocks. |
| `pipelinec/matrix_multiplication_n.py` | Variable size N (1..`MAX_N`, 8 by default); N arrives over UART. Uses the optimized scheme. |
| `pipelinec/matrix_multiplication_parallel.py` | Batch of K 3×3 products computed at once (a study of whether parallelism helps with UART as the data channel). |
| `pipelinec/sim/` | Native pypeline simulation testbenches (one per version). |
| `pipelinec/matrix_multiplication.cst` | Tang Nano 20K pin constraints. |
| `verilog/` | Verilog version (`matrix_mul.v`, `matrix_mul_opt.v`), constraints and `build_gowin.sh` to synthesize with Gowin. |
| `app/matrix_multiplication.py` | PC client: sends random matrices over the serial port and checks the result. |

## Usage

```bash
source ../env.sh                       # from matrix_multiplication/ (defines PIPELINEC_DIR)

# Native simulation (no board needed)
cd pipelinec/sim
python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_matrix_mul.py --run all
MATRIX_SIZES=3,2,8 python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_matrix_mul_n.py --run all
NUM_PRODUCTS=4 BATCHES=2 python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_matrix_mul_parallel.py --run all

# Synthesis for the board (in an empty working directory)
$PIPELINEC_DIR/src/pypelinec $PWD/../matrix_multiplication.py --part "GW2AR-LV18QN88C8/I7" --pins $PWD/../matrix_multiplication.cst

# PC client (/dev/ttyUSB1 is the UART; ttyUSB0 is the JTAG)
python3 ../../app/matrix_multiplication.py -p /dev/ttyUSB1 -n 3
```

To synthesize the Verilog version: `verilog/build_gowin.sh <matrix_mul|matrix_mul_opt> [N]`. The `_n` and `_parallel` simulations are configured with the environment variables `MATRIX_SIZES`, `SEED`, `NUM_PRODUCTS` and `BATCHES`.

## Results

Gowin synthesis and place & route for the GW2AR-18 (24 DSP, 15,750 registers). All versions meet the 27 MHz clock with a wide margin.

### Base vs. optimized (3×3, PipelineC)

| | Base | Optimized |
|---|---|---|
| DSP | 18 / 24 (75 %) | **2 / 24 (9 %)** |
| LUT + ALU | 375 | 568 |
| Registers | 350 | 519 |
| Fmax | 125.2 MHz | 79.1 MHz |
| Estimated power | 140.3 mW | 124.0 mW |

The optimization computes one element of C per cycle (9 cycles instead of 1), cutting DSP blocks by 89 % at the cost of more logic and registers. Power improves only slightly because ~123 mW is the FPGA's static consumption.

### Variable size (`_n`)

Native simulation passes with N = 3, N = 8 (maximum), mixed sizes (`3,2,8,5,1`) and invalid sizes (`0,9,6,4`, which are ignored), with no extra bytes. Verilator/VHDL also passes (`8,3,1,0`, 148 of 148 bytes). The resource figures for `MAX_N = 8` are an arithmetic estimate (≈ 4 DSP and ~19 % of the registers), **not measured with Gowin**.

### PipelineC vs. hand-written Verilog (3×3)

| | PipelineC base | Verilog base | PipelineC optimized | Verilog optimized |
|---|---|---|---|---|
| DSP | 18 | 18 | 2 | 2 |
| LUT + ALU | 375 | 410 | 568 | 618 |
| Registers | 350 | 366 | 519 | 534 |
| Fmax (MHz) | 125.2 | 82.6 | 79.1 | 89.7 |

DSP counts are identical and logic is of the same order (PipelineC ≈ 9 % smaller, partly due to design differences). The Fmax differences are inconclusive (a single measurement per design).

### Batch of K parallel products

| K | Cycles per product | LUT + ALU | DSP | Fmax (MHz) |
|---|---|---|---|---|
| 1 | 15,529 | 568 | 2 | 79.1 |
| 2 | 15,550 | 1,010 | 4 | 86.5 |
| 4 | 15,560 | 1,720 | 8 | 75.5 |
| 8 | 15,565 | 3,160 | 16 | 61.6 |
| 12 | – | 4,790 | 24 (100 %) | 53.8 |

**Computing in parallel does not speed anything up:** the time per product (~1,735 products/s) is set by the UART (10 bits per byte at 1 Mbaud), and computing a whole batch takes ~14 cycles, under 0.1 % of the total. Resources grow linearly with K with no performance gain. With K = 1 Gowin fails with an internal error, although it is equivalent to the already-measured optimized version.

## Status and limitations

- **No design has been tested on the board**; verification is by native simulation and, in some cases, Verilator.
- The Verilator testbenches (`.cpp`) and `run_verilator.sh` are not in this repository.
- Synthesis results are a single measurement per configuration, and power is an estimate with default activity rates.