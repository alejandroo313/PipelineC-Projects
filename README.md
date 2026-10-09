# PipelineC Projects

Personal collection of **hardware programming** projects (FPGA/ASIC) written with [**PipelineC**](https://github.com/JulianKemmerer/PipelineC), a C-like hardware description language that compiles to VHDL/Verilog and automatically pipelines the design to reach the target clock frequency.

> Official PipelineC repository: <https://github.com/JulianKemmerer/PipelineC>

The designs are written in PipelineC's Python front end (`pypeline`) and target the **Sipeed Tang Nano 20K** board (Gowin GW2AR-18, 27 MHz clock).

## Projects

| Project | Description | Status |
|---------|-------------|--------|
| [`uart/`](uart/) | UART protocol (8N1 transmitter and receiver) with an echo example, plus a hand-written Verilog version. | Done |
| [`matrix_multiplication/`](matrix_multiplication/) | Matrix multiplication over UART: base, DSP-optimized, variable-size and parallel-batch versions, compared against Verilog. | Done |

## My board: Sipeed Tang Nano 20K

Reference data ([Sipeed wiki](https://wiki.sipeed.com/hardware/en/tang/tang-nano-20k/nano-20k.html)).

| | |
|---|---|
| **FPGA** | Gowin GW2AR-LV18QN88C8/I7 |
| **Clock** | 27 MHz crystal (+ MS5351 clock generator, 3 extra clocks) |
| **Logic** | 20,736 LUT4 · 15,552 flip-flops |
| **Memory** | 828 Kbit B-SRAM (46 blocks) · 41,472 bit S-SRAM |
| **Arithmetic** | 48 18×18 multipliers (reported as 24 `MULTADDALU18X18` DSP blocks by Gowin) |
| **Clocking / I/O** | 2 PLLs · 8 I/O banks |
| **External memory** | 64 Mbit SDRAM (32-bit SDR) · 64 Mbit flash (bitstream) |
| **USB / debug** | BL616: JTAG + USB-to-UART + USB-to-SPI (`/dev/ttyUSB0` = JTAG, `/dev/ttyUSB1` = UART) |
| **User I/O** | 2 buttons · 6 LEDs · 1 WS2812 RGB LED |
| **Interfaces** | HDMI · 40-pin RGB LCD connector · microSD (TF) slot · PCM audio amplifier (MAX98357A) |
| **Pins used in this repo** | clock = 4 · UART RX = 70 · UART TX = 69 |

## Repository layout

```
pipelinec/
├── uart/                      # UART in PipelineC (pipelinec/) and Verilog (verilog/)
├── matrix_multiplication/     # designs (pipelinec/), PC client (app/), Verilog (verilog/)
├── env.sh                     # environment setup (PIPELINEC_DIR and tool PATH)
└── README.md
```

Each project is self-contained and has its own `README.md`. The only cross-dependency is that `matrix_multiplication/` reuses the UART modules from `uart/pipelinec/` (`uart_tx.py`, `uart_rx.py`): its designs add `../../uart/pipelinec` to `sys.path`, so there are no duplicated copies.

## Requirements

- [PipelineC](https://github.com/JulianKemmerer/PipelineC) (see its documentation for installation).
- A synthesis/simulation toolchain: Gowin EDA (`gw_sh`) or an open-source flow (Yosys + nextpnr), and optionally Verilator/GHDL.
- [`openFPGALoader`](https://github.com/trabucayre/openFPGALoader) to program the board, and `picocom` or `pyserial` to talk to it.

## Usage

```bash
source env.sh    # defines PIPELINEC_DIR (path to your PipelineC clone) and the tools' PATH
```

### `run.py`: build, flash and measure

`run.py` drives the whole flow (PipelineC → Gowin EDA → openFPGALoader) from one command, using each project's `project.toml`:

```bash
python run.py help                      # full help
python run.py --list                    # projects and subprojects
python run.py matmul                    # build + flash (SRAM) + bench
python run.py matmul --build            # synthesize only
python run.py matmul/parallel --build   # a subproject (matmul/base|optimized|n|parallel)
python run.py matmul --flash --persist  # write to the board's flash instead of SRAM
python run.py matmul --build --force    # ignore the synthesis cache
python run.py matmul --board tangnano9k # another board (see below)
```

- **Cache and output:** builds go to `build/<project>/<hash>/` (bitstream: `top/impl/pnr/top.fs`, plus `metrics.json` with Fmax and utilization). The hash covers the sources, pins, part and PipelineC revision, so nothing is resynthesized unless something changed.
- **Subprojects:** a `project.toml` can declare `[variants.<name>]` tables. `matmul` is an alias of its `default_variant` (`matmul/optimized`).
- **Boards:** `--board` takes an openFPGALoader board name (`openFPGALoader --list-boards`) and defaults to `tangnano20k`. The top-level keys of each `project.toml` describe the Tang Nano 20K; for any other board add a `[boards.<board>]` table overriding at least `part` and `pins` (and `clk_mhz` if the clock is not 27 MHz). Without that table `run.py` aborts instead of reusing the 20K's part and pins. Only the Tang Nano 20K has been tested.
- **Bench:** `--bench` needs the project's `host.py` (not written yet) and appends a row to `results/results.csv`.

### Manual commands

Native simulation and synthesis for the Tang Nano 20K without `run.py`:

```bash
# UART (echo)
cd uart/pipelinec/sim && python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_uart_echo.py --run all
cd .. && $PIPELINEC_DIR/src/pypelinec uart_echo_top.py --part "GW2AR-LV18QN88C8/I7" --pins uart_echo_top.cst

# Matrix multiplication
cd matrix_multiplication/pipelinec/sim && python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_matrix_mul.py --run all
```

See each project's README for details.

## Adding a new project

1. Create a folder with the project name at the root.
2. Add its source code and its own `README.md`.
3. List it in the projects table above.
