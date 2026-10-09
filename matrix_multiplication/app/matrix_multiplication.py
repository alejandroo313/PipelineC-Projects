"""Cliente PC: envia dos matrices aleatorias uint16 a la FPGA por UART y comprueba C = A * B.

Protocolo (ver pipelinec/matrix_multiplication.py), uint16 little-endian, fila a fila:

    PC -> FPGA : [N (1 byte, solo con --con-n)] + A (N*N*2 bytes) + B (N*N*2 bytes)
    FPGA -> PC : C (N*N*2 bytes)

Los productos se acumulan en uint16, asi que el resultado esperado es modulo 65536.

Para sintetizar el diseno de la FPGA:
    source env.sh   # desde la raíz del repositorio
    cd matrix_multiplication/pipelinec
    $PIPELINEC_DIR/src/pypelinec matrix_multiplication.py --part "GW2AR-LV18QN88C8/I7" --pins matrix_multiplication.cst
"""
import argparse
import random
import struct

import serial

DEFAULT_PORT = "/dev/ttyUSB1"   # ttyUSB0 es JTAG: no escribir ahi
DEFAULT_BAUD = 1_000_000        # debe coincidir con BAUD_RATE de la FPGA


def random_matrix(n):
    return [[random.randint(0, 0xFFFF) for _ in range(n)] for _ in range(n)]


def pack_matrix(m):
    """Matriz -> bytes uint16 little-endian, fila a fila."""
    flat = [x for row in m for x in row]
    return struct.pack(f"<{len(flat)}H", *flat)


def unpack_matrix(data, n):
    flat = struct.unpack(f"<{n * n}H", data)
    return [list(flat[i * n:(i + 1) * n]) for i in range(n)]


def expected_product(A, B):
    n = len(A)
    return [[sum(A[i][k] * B[k][j] for k in range(n)) & 0xFFFF for j in range(n)]
            for i in range(n)]


def matrix_multiply(ser, A, B, con_n=False):
    """Envia A y B a la FPGA y devuelve la matriz C recibida."""
    n = len(A)
    payload = (bytes([n]) if con_n else b"") + pack_matrix(A) + pack_matrix(B)

    ser.reset_input_buffer()
    ser.write(payload)
    ser.flush()

    expected_len = n * n * 2
    data = ser.read(expected_len)
    if len(data) != expected_len:
        raise TimeoutError(f"La FPGA devolvio {len(data)} de {expected_len} bytes")
    return unpack_matrix(data, n)


def print_matrix(name, m):
    print(f"{name}:")
    for row in m:
        print("  " + " ".join(f"{x:5d}" for x in row))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-p", "--port", default=DEFAULT_PORT)
    p.add_argument("-b", "--baud", type=int, default=DEFAULT_BAUD)
    p.add_argument("-n", type=int, default=3, help="tamano de la matriz (por defecto 3)")
    p.add_argument("--con-n", action="store_true",
                   help="anteponer el byte N (para FPGA/matrix_multiplication_n.py)")
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    A = random_matrix(args.n)
    B = random_matrix(args.n)
    print_matrix("A", A)
    print_matrix("B", B)

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        C = matrix_multiply(ser, A, B, args.con_n)

    print_matrix("C (FPGA)", C)

    expected = expected_product(A, B)
    if C == expected:
        print("OK: coincide con el producto calculado en el PC")
    else:
        print_matrix("C (esperada)", expected)
        raise SystemExit("ERROR: el resultado de la FPGA no coincide")


if __name__ == "__main__":
    main()
