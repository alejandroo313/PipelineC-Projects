"""Multiplicador de matrices 3x3 (uint16) por UART para la Tang Nano 20K.

Protocolo (8N1, ver BAUD_RATE), elementos uint16 en little-endian (byte bajo primero),
recorridos fila a fila:

    PC -> FPGA : matriz A (18 bytes) + matriz B (18 bytes)
    FPGA -> PC : matriz C = A * B   (18 bytes)

Flujo del programa (maquina de estados):

    RECEIVE --(36 bytes)--> COMPUTE --(1 ciclo)--> SEND --(18 bytes)--> RECEIVE
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

MATRIX_SIZE = 3
BYTES_PER_ELEMENT = 2                                           # Elementos uint16_t
MATRIX_BYTES = MATRIX_SIZE * MATRIX_SIZE * BYTES_PER_ELEMENT    # Bytes de UNA matriz (18)
INPUT_BYTES = 2 * MATRIX_BYTES                                  # Bytes de A + B (36)
OUTPUT_BYTES = MATRIX_BYTES                                     # Bytes de C (18)

matrix_t = uint16_t[MATRIX_SIZE][MATRIX_SIZE]
ZERO_MATRIX = [[0] * MATRIX_SIZE for _ in range(MATRIX_SIZE)]


# ------------------------------------------------------------------ tipos
@struct
class cell_t(NamedTuple):
    """Posicion (fila, columna) de un elemento dentro de una matriz."""
    row: uint3_t
    col: uint3_t


@enum
class mm_state_t(IntEnum):
    RECEIVE = 0     # Recibiendo A y B del PC (y esperando el primer byte)
    COMPUTE = 1     # Calculando C = A * B
    SEND = 2        # Enviando C al PC


uart_send, uart_tx_t = make_uart_tx(CLK_FREQ_MHZ, BAUD_RATE)
uart_recv, uart_rx_t = make_uart_rx(CLK_FREQ_MHZ, BAUD_RATE)

# ------------------------------------------------------------------ pines
rx_pin: Input[uint1_t]     # Pin RX de la FPGA (entrada serie desde el PC)
tx_pin: Output[uint1_t]    # Pin TX de la FPGA (salida serie hacia el PC)


# ------------------------------------------------------------------ logica combinacional
@hw_func
def multiply_matrices(a: matrix_t, b: matrix_t) -> matrix_t:
    """C = A * B (producto de matrices, aritmetica modulo 2^16)."""
    c: matrix_t
    for row in range(MATRIX_SIZE):
        for col in range(MATRIX_SIZE):
            acc: uint16_t = 0
            for k in range(MATRIX_SIZE):
                acc += a[row][k] * b[k][col]
            c[row][col] = acc
    return c


@hw_func
def next_cell(cell: cell_t) -> cell_t:
    """Siguiente elemento recorriendo la matriz fila a fila (la ultima columna salta de fila)."""
    nxt: cell_t = cell
    nxt.col = cell.col + 1
    if nxt.col == MATRIX_SIZE:
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
    if s == mm_state_t.COMPUTE:
        rv = 1
    elif s == mm_state_t.SEND:
        rv = 2
    return rv


@hw_func
def print_matrix(m: matrix_t) -> uint1_t:
    for row in range(MATRIX_SIZE):
        sim_print(f"        {m[row][0]} {m[row][1]} {m[row][2]}")
    return 0


# ------------------------------------------------------------------ programa principal
@MAIN(CLK_FREQ_MHZ)
def main():
    # --- Maquina de estados y contadores
    state: Reg[mm_state_t]              # Fase actual (0=RECEIVE 1=COMPUTE 2=SEND)
    byte_count: Reg[uint8_t] = 0        # Bytes recibidos (RECEIVE) o enviados (SEND) en la fase actual
    cell: Reg[cell_t] = cell_t(row=0, col=0)    # Elemento de la matriz que se escribe/lee ahora

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

    # DEBUG (solo simulacion). Traza completa por ciclo (muy ruidosa, descomentar si hace falta):
    # sim_print(f"state={uint2_t(state)} byte_count={byte_count} row={cell.row} col={cell.col} rx_valid={rx.rx_data_valid} tx_ready={tx.ready} tx_valid={tx_data_valid}")
    if rx.rx_data_valid:
        sim_print(f"[RX ] byte={rx.rx_data} | state={uint2_t(state)} byte_count={byte_count} row={cell.row} col={cell.col}")
    if tx_data_valid:
        sim_print(f"[TX ] valid=1 tx_data={tx_data} tx_ready={tx.ready} | state={uint2_t(state)} byte_count={byte_count}")

    if state == mm_state_t.RECEIVE:
        if rx.rx_data_valid:
            # Los primeros MATRIX_BYTES bytes son A y los siguientes B
            if byte_count < MATRIX_BYTES:
                sim_print(f"[MEM] A[{cell.row}][{cell.col}]: antes={matrix_a[cell.row][cell.col]} byte_nuevo={rx.rx_data} (byte_count={byte_count})")
                matrix_a[cell.row][cell.col] = shift_in_byte(matrix_a[cell.row][cell.col], rx.rx_data)
            else:
                sim_print(f"[MEM] B[{cell.row}][{cell.col}]: antes={matrix_b[cell.row][cell.col]} byte_nuevo={rx.rx_data} (byte_count={byte_count})")
                matrix_b[cell.row][cell.col] = shift_in_byte(matrix_b[cell.row][cell.col], rx.rx_data)
            byte_count += 1
            # Elemento completo (todos sus bytes recibidos): pasa al siguiente
            if byte_count % BYTES_PER_ELEMENT == 0:
                cell = next_cell(cell)
        if byte_count == MATRIX_BYTES:
            # A completa: B empieza otra vez en el elemento [0][0]
            cell = cell_t(row=0, col=0)
        elif byte_count == INPUT_BYTES:
            sim_print("[FSM] RECEIVE -> COMPUTE")
            cell = cell_t(row=0, col=0)
            byte_count = 0
            state = mm_state_t.COMPUTE
    elif state == mm_state_t.COMPUTE:
        sim_print("[COMPUTE] Matriz A:")
        print_matrix(matrix_a)
        sim_print("[COMPUTE] Matriz B:")
        print_matrix(matrix_b)
        matrix_c = multiply_matrices(matrix_a, matrix_b)
        sim_print("[COMPUTE] Matriz C = A * B:")
        print_matrix(matrix_c)
        sim_print("[FSM] COMPUTE -> SEND")
        state = mm_state_t.SEND
    elif state == mm_state_t.SEND:
        # Bytes pares = byte bajo del elemento, impares = byte alto
        send_high_byte: uint1_t = (byte_count % BYTES_PER_ELEMENT == 1)
        tx_data = select_byte(matrix_c[cell.row][cell.col], send_high_byte)
        tx_data_valid = 1
        if tx_accepted:
            sim_print(f"[SEND] byte {byte_count} aceptado por el TX (tx_data={tx_data}) -> byte_count={byte_count + 1}")
            byte_count += 1
            if byte_count % BYTES_PER_ELEMENT == 0:
                cell = next_cell(cell)
            if byte_count == OUTPUT_BYTES:
                # Todos los bytes entregados al TX; el ultimo termina de salir por si solo
                sim_print("[FSM] SEND -> RECEIVE")
                byte_count = 0
                cell = cell_t(row=0, col=0)  # La siguiente trama empieza en el elemento [0][0]
                tx_data_valid = 0            # Nada mas que enviar: el TX no debe repetir el ultimo byte
                state = mm_state_t.RECEIVE

    tx_pin = tx.tx_out
