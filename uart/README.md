# UART

Hardware implementation of an **8N1 UART** (8 data bits, no parity, 1 stop bit), written in PipelineC and, as a reference, in Verilog. It is the PC communication layer for the other projects in this repository (for example, [`matrix_multiplication/`](../matrix_multiplication/)).

- Board: Sipeed Tang Nano 20K (27 MHz clock).
- Default baud rate: 115200 (configurable when the modules are created).
- Pins: RX = 70 and TX = 69, wired to the board's USB-serial bridge (BL616).

## Contents

### `pipelinec/`

| File | Description |
|------|-------------|
| `uart_common.py` | Shared constants and calculations: default baud rate and clock cycles per bit (`clk_per_bit`). |
| `uart_tx.py` | Transmitter. `make_uart_tx(clk_mhz, baud)` returns the hardware function `uart_tx(tx_data, tx_data_valid)`, which returns `tx_out` and `ready`. Valid/ready protocol: a byte is accepted on the cycle where both `valid` and `ready` are 1. |
| `uart_rx.py` | Receiver. `make_uart_rx(clk_mhz, baud)` returns `uart_rx(rx_in, rx_data_ready)`, which returns `rx_data` and `rx_data_valid`. It synchronizes the input with 3 registers, detects the falling edge of the start bit and samples in the middle of each bit. |
| `uart_echo.py` | Reusable echo module: connects RX and TX with a small FSM. The FPGA never transmits on its own initiative, it only answers what it receives. Anything received during a startup window (50 ms by default) is discarded. |
| `uart_echo_top.py` + `uart_echo_top.cst` | **Example top**: everything the PC sends on RX comes back on TX. Includes the pin constraints file. |
| `sim/sim_uart_echo.py` | Testbench for pypeline's native simulation: sends bytes on RX and checks that they come back unchanged. |

### `verilog/`

Hand-written version, used as a reference and to compare against the PipelineC-generated one.

| File | Description |
|------|-------------|
| `uart_tx.v`, `uart_rx.v` | Parameterizable UART transmitter and receiver (`CLK_FRE`, `BAUD_RATE`). |
| `uart_top.v` | Example (module `uart_test`): sends a greeting message every second and echoes back on TX everything it receives. |

The pin constraints file for the Verilog version is not included; the board pins are the same (clock 4, RX 70, TX 69).

## Usage

### 1. Simulate (no board needed)

```bash
source ../env.sh
cd pipelinec/sim
python3 $PIPELINEC_DIR/src/pypeline_sim.py sim_uart_echo.py --run all
```

It should end with `OK: el eco coincide` (the echo matches).

### 2. Synthesize and load the echo example

```bash
cd uart/pipelinec
$PIPELINEC_DIR/src/pypelinec uart_echo_top.py --part "GW2AR-LV18QN88C8/I7" --pins uart_echo_top.cst
openFPGALoader -b tangnano20k <path>/top.fs
```

`pypelinec` prints the path of the generated bitstream at the end.

### 3. Try it with picocom

The board exposes two ports: `/dev/ttyUSB0` is the JTAG (do not write to it) and `/dev/ttyUSB1` is the UART.

```bash
picocom -b 115200 /dev/ttyUSB1
```

Whatever you type is sent to the FPGA and comes back on TX, so you will see each typed character once (picocom has no local echo). To exit: `Ctrl-A`, then `Ctrl-X`.

If you lack permission to open the port, add your user to the `dialout` group (`sudo usermod -aG dialout $USER`) and log in again.

Quick test without an interactive terminal, using pyserial:

```bash
python3 -c "import serial; s=serial.Serial('/dev/ttyUSB1', 115200, timeout=1); s.write(b'hola'); print(s.read(4))"
```

### 4. Use the UART in another design

```python
from uart_tx import make_uart_tx
from uart_rx import make_uart_rx

uart_send, uart_tx_t = make_uart_tx(27.0, 115200)
uart_recv, uart_rx_t = make_uart_rx(27.0, 115200)
```

Add `uart/pipelinec` to `sys.path` before importing, as the `matrix_multiplication/` designs do.

## Notes

- The FPGA transmits nothing after being loaded: on the Tang Nano 20K, transmitting right after configuration locks up the BL616 USB bridge. That is why the echo only answers when it receives a byte.
- During the first 50 ms after startup, anything arriving on RX is discarded (a spurious byte was observed on hardware when the FPGA was configured).
- Verification done: native simulation of the echo (`sim/sim_uart_echo.py`). The synthesis and picocom steps in this README have not been run from this repository.
