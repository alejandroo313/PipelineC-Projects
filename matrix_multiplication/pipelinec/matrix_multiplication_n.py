"""Multiplicador de matrices NxN (uint16) por UART para la Tang Nano 20K.

Generalizacion de matrix_multiplication.py: el tamaño N llega por UART como primer byte.

Protocolo (8N1, ver BAUD_RATE), elementos uint16 en little-endian (byte bajo primero),
recorridos fila a fila:

    PC -> FPGA : N (1 byte, 1..MAX_N) + matriz A (N*N*2 bytes) + matriz B (N*N*2 bytes)
    FPGA -> PC : matriz C = A * B (N*N*2 bytes)

El hardware se dimensiona para MAX_N (arrays y sumador); N solo decide cuantos elementos
se usan. Un primer byte fuera de 1..MAX_N se ignora y se sigue esperando.

COMPUTE calcula UN elemento de C por ciclo (hasta MAX_N productos en paralelo, unos MAX_N/2
bloques DSP) y tarda N*N ciclos. Subir MAX_N crece el hardware de forma lineal en
multiplicadores (no con N^3) y cuadratica en registros (3 matrices de MAX_N*MAX_N uint16).

Flujo del programa (maquina de estados):

    RECEIVE_SIZE --(1 byte)--> RECEIVE_MATRICES --(2*N*N*2 bytes)--> COMPUTE
         ^                                                              |
         +---(N*N*2 bytes)--- SEND <---------(N*N ciclos)---------------+
"""
import os
import sys

# La UART vive en ../../uart/pipelinec (compartida con el proyecto uart)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "uart", "pipelinec"))
from pypeline import *
from enum import IntEnum
from uart_tx import make_uart_tx
from uart_rx import make_uart_rx

# ------------------------------------------------------------------ configuracion
CLK_FREQ_MHZ = 27.0     # Reloj de la Tang Nano 20K
BAUD_RATE = 1000000

MAX_N = 8               # Tamaño maximo de matriz soportado por el hardware (1..15)
BYTES_PER_ELEMENT = 2   # Elementos uint16_t

size_t = uint4_t        # Tamaño N de la matriz y indices fila/columna (admite hasta 15)
matrix_t = uint16_t[MAX_N][MAX_N]
ZERO_MATRIX = [[0] * MAX_N for _ in range(MAX_N)]


# ------------------------------------------------------------------ tipos
@struct
class cell_t(NamedTuple):
    """Posicion (fila, columna) de un elemento dentro de una matriz."""
    row: size_t
    col: size_t


@enum
class mm_state_t(IntEnum):
    RECEIVE_SIZE = 0        # Esperando el primer byte de la trama: el tamaño N
    RECEIVE_MATRICES = 1    # Recibiendo A y B del PC
    COMPUTE = 2             # Calculando C = A * B
    SEND = 3                # Enviando C al PC


uart_send, uart_tx_t = make_uart_tx(CLK_FREQ_MHZ, BAUD_RATE)
uart_recv, uart_rx_t = make_uart_rx(CLK_FREQ_MHZ, BAUD_RATE)

# ------------------------------------------------------------------ pines
rx_pin: Input[uint1_t]     # Pin RX de la FPGA (entrada serie desde el PC)
tx_pin: Output[uint1_t]    # Pin TX de la FPGA (salida serie hacia el PC)


# ------------------------------------------------------------------ logica combinacional
@hw_func
def dot_product(row: size_t, col: size_t, a: matrix_t, b: matrix_t, n: size_t) -> uint16_t:
    """Elemento C[row][col] de C = A * B para matrices NxN: producto de la fila `row` de A por la
    columna `col` de B (aritmetica modulo 2^16).

    Se instancian MAX_N productos, pero la suma solo incluye los k < n; lo que haya mas alla de
    N en A y B no influye."""
    acc: uint16_t = 0
    for k in range(MAX_N):
        if k < n:
            acc += a[row][k] * b[k][col]
    return acc


@hw_func
def next_cell(cell: cell_t, n: size_t) -> cell_t:
    """Siguiente elemento recorriendo la matriz NxN fila a fila (la ultima columna salta de fila)."""
    nxt: cell_t = cell
    nxt.col = cell.col + 1
    if nxt.col == n:
        nxt.col = 0
        nxt.row = cell.row + 1
    return nxt


@hw_func
def shift_in_byte(element: uint16_t, new_byte: uint8_t) -> uint16_t:
    """Mete new_byte por la parte alta del elemento y desplaza el resto a la derecha.

    Tras recibir los 2 bytes de un elemento (byte bajo primero) el primero queda abajo y el
    segundo arriba; lo que hubiera antes en el elemento ya ha salido por la derecha."""
    wide_byte: uint16_t = new_byte
    return (wide_byte << 8) | (element >> 8)


@hw_func
def select_byte(element: uint16_t, send_high: uint1_t) -> uint8_t:
    """Byte bajo (send_high=0) o byte alto (send_high=1) de un elemento."""
    rv: uint8_t = element & 0xFF
    if send_high:
        rv = element >> 8
    return rv


# ------------------------------------------------------------------ depuracion (solo simulacion)
@cast
def mm_state_to_uint2(s: mm_state_t) -> uint2_t:
    """Permite imprimir el estado como numero: uint2_t(state).
    (La elaboracion a VHDL no sabe interpolar un enum directamente en un sim_print.)"""
    rv: uint2_t = 0
    if s == mm_state_t.RECEIVE_MATRICES:
        rv = 1
    elif s == mm_state_t.COMPUTE:
        rv = 2
    elif s == mm_state_t.SEND:
        rv = 3
    return rv


# ------------------------------------------------------------------ programa principal
@MAIN(CLK_FREQ_MHZ)
def main():
    # --- Maquina de estados y contadores
    state: Reg[mm_state_t]              # Fase actual (0=RECEIVE_SIZE 1=RECEIVE_MATRICES 2=COMPUTE 3=SEND)
    byte_count: Reg[uint16_t] = 0       # Bytes recibidos (RECEIVE_MATRICES) o enviados (SEND) en la fase actual
    cell: Reg[cell_t] = cell_t(row=0, col=0)    # Elemento de la matriz que se escribe/calcula/lee ahora

    # --- Tamaño de la trama actual (se fija al recibir el primer byte)
    size: Reg[size_t] = 1               # N
    matrix_bytes: Reg[uint16_t] = 0     # Bytes de UNA matriz = N * N * BYTES_PER_ELEMENT

    # --- Datos
    matrix_a: Reg[matrix_t] = ZERO_MATRIX
    matrix_b: Reg[matrix_t] = ZERO_MATRIX
    matrix_c: Reg[matrix_t] = ZERO_MATRIX

    # --- Interfaz con el transmisor
    tx_data: Reg[uint8_t] = 0           # Byte a enviar
    tx_data_valid: Reg[uint1_t] = 0     # 1 = hay un byte listo en tx_data

    # --- Modulos UART (el receptor siempre esta listo para recibir: ready = 1)
    rx: uart_rx_t = uart_recv(rx_pin, 1)
    tx: uart_tx_t = uart_send(tx_data, tx_data_valid)

    # El TX acepta el byte en el ciclo en que tx_data_valid y tx.ready valen 1 a la vez. Se calcula
    # aqui, ANTES de asignar tx_data_valid, porque tras asignar un Reg su nombre ya vale el valor nuevo.
    tx_accepted: uint1_t = tx_data_valid & tx.ready

    # Derivados del tamaño de la trama actual
    input_bytes: uint16_t = matrix_bytes + matrix_bytes     # Bytes de A + B
    last_index: size_t = size - 1                           # Fila/columna del ultimo elemento (N-1)
    requested_size: uint16_t = rx.rx_data                   # Primer byte de la trama (solo vale en RECEIVE_SIZE)

    # DEBUG (solo simulacion). Traza completa por ciclo (muy ruidosa, descomentar si hace falta):
    # sim_print(f"state={uint2_t(state)} size={size} byte_count={byte_count} row={cell.row} col={cell.col} rx_valid={rx.rx_data_valid} tx_ready={tx.ready} tx_valid={tx_data_valid}")
    if rx.rx_data_valid:
        sim_print(f"[RX ] byte={rx.rx_data} | state={uint2_t(state)} size={size} byte_count={byte_count} row={cell.row} col={cell.col}")
    if tx_data_valid:
        sim_print(f"[TX ] valid=1 tx_data={tx_data} tx_ready={tx.ready} | state={uint2_t(state)} byte_count={byte_count}")

    if state == mm_state_t.RECEIVE_SIZE:
        if rx.rx_data_valid:
            # Solo se aceptan tamaños 1..MAX_N; cualquier otro primer byte se ignora
            if requested_size == 0:
                sim_print("[SIZE] tamano 0 no valido: se ignora el byte")
            elif requested_size > MAX_N:
                sim_print(f"[SIZE] tamano {requested_size} > MAX_N: se ignora el byte")
            else:
                sim_print(f"[SIZE] N={requested_size} | [FSM] RECEIVE_SIZE -> RECEIVE_MATRICES")
                size = requested_size
                matrix_bytes = requested_size * requested_size * BYTES_PER_ELEMENT
                byte_count = 0
                cell = cell_t(row=0, col=0)
                state = mm_state_t.RECEIVE_MATRICES
    elif state == mm_state_t.RECEIVE_MATRICES:
        if rx.rx_data_valid:
            # Los primeros matrix_bytes bytes son A y los siguientes B
            if byte_count < matrix_bytes:
                sim_print(f"[MEM] A[{cell.row}][{cell.col}]: antes={matrix_a[cell.row][cell.col]} byte_nuevo={rx.rx_data} (byte_count={byte_count})")
                matrix_a[cell.row][cell.col] = shift_in_byte(matrix_a[cell.row][cell.col], rx.rx_data)
            else:
                sim_print(f"[MEM] B[{cell.row}][{cell.col}]: antes={matrix_b[cell.row][cell.col]} byte_nuevo={rx.rx_data} (byte_count={byte_count})")
                matrix_b[cell.row][cell.col] = shift_in_byte(matrix_b[cell.row][cell.col], rx.rx_data)
            byte_count += 1
            # Elemento completo (todos sus bytes recibidos): pasa al siguiente
            if byte_count % BYTES_PER_ELEMENT == 0:
                cell = next_cell(cell, size)
        if byte_count == matrix_bytes:
            # A completa: B empieza otra vez en el elemento [0][0]
            cell = cell_t(row=0, col=0)
        elif byte_count == input_bytes:
            sim_print("[FSM] RECEIVE_MATRICES -> COMPUTE")
            cell = cell_t(row=0, col=0)
            byte_count = 0
            state = mm_state_t.COMPUTE
    elif state == mm_state_t.COMPUTE:
        # Un elemento de C por ciclo, recorriendo la matriz fila a fila con `cell`
        c_element: uint16_t = dot_product(cell.row, cell.col, matrix_a, matrix_b, size)
        matrix_c[cell.row][cell.col] = c_element
        sim_print(f"[COMPUTE] C[{cell.row}][{cell.col}] = {c_element}")
        if cell.row == last_index and cell.col == last_index:
            sim_print("[FSM] COMPUTE -> SEND")
            cell = cell_t(row=0, col=0)
            byte_count = 0
            state = mm_state_t.SEND
        else:
            cell = next_cell(cell, size)
    elif state == mm_state_t.SEND:
        # Bytes pares = byte bajo del elemento, impares = byte alto
        send_high_byte: uint1_t = (byte_count % BYTES_PER_ELEMENT == 1)
        tx_data = select_byte(matrix_c[cell.row][cell.col], send_high_byte)
        tx_data_valid = 1
        if tx_accepted:
            sim_print(f"[SEND] byte {byte_count} aceptado por el TX (tx_data={tx_data}) -> byte_count={byte_count + 1}")
            byte_count += 1
            if byte_count % BYTES_PER_ELEMENT == 0:
                cell = next_cell(cell, size)
            if byte_count == matrix_bytes:
                # Todos los bytes entregados al TX; el ultimo termina de salir por si solo
                sim_print("[FSM] SEND -> RECEIVE_SIZE")
                byte_count = 0
                cell = cell_t(row=0, col=0)  # La siguiente trama empieza en el elemento [0][0]
                tx_data_valid = 0            # Nada mas que enviar: el TX no debe repetir el ultimo byte
                state = mm_state_t.RECEIVE_SIZE

    tx_pin = tx.tx_out
