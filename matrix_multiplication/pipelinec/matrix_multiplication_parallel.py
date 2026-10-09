"""Multiplicador de un LOTE de K pares de matrices 3x3 (uint16) en paralelo, por UART.

Calcula K productos independientes C_i = A_i * B_i con K unidades de calculo funcionando a la
vez. Parte de matrix_multiplication_optimized.py (un elemento de C por ciclo y por unidad).

K = NUM_PRODUCTS. Se fija con la variable de entorno del mismo nombre (por defecto 4):

    NUM_PRODUCTS=8 python3 ... matrix_multiplication_parallel.py

Protocolo (8N1, ver BAUD_RATE), elementos uint16 en little-endian (byte bajo primero),
recorridos fila a fila:

    PC -> FPGA : A_0 B_0 A_1 B_1 ... A_{K-1} B_{K-1}    (K * 36 bytes)
    FPGA -> PC : C_0 C_1 ... C_{K-1}                    (K * 18 bytes)

Flujo del programa (maquina de estados):

    RECEIVE --(K*36 bytes)--> COMPUTE --(9 ciclos)--> SEND --(K*18 bytes)--> RECEIVE

COMPUTE tarda 9 ciclos para TODO el lote (las K unidades calculan a la vez el mismo elemento
[row][col] de sus matrices), igual que para un solo producto. El tiempo total lo domina la UART:
transmitir 54 bytes por producto cuesta ~14 580 ciclos y calcularlo, 9.
"""
import os
import sys
from pypeline import *
from enum import IntEnum

# La UART vive en ../../uart/pipelinec (compartida con el proyecto uart)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "uart", "pipelinec"))
from uart_tx import make_uart_tx
from uart_rx import make_uart_rx

# ------------------------------------------------------------------ configuracion
CLK_FREQ_MHZ = 27.0     # Reloj de la Tang Nano 20K
BAUD_RATE = 1000000

NUM_PRODUCTS = int(os.environ.get("NUM_PRODUCTS", "4"))     # K: productos que se calculan a la vez (1..15)
MATRIX_SIZE = 3
BYTES_PER_ELEMENT = 2                                           # Elementos uint16_t
MATRIX_BYTES = MATRIX_SIZE * MATRIX_SIZE * BYTES_PER_ELEMENT    # Bytes de UNA matriz (18)
PAIR_BYTES = 2 * MATRIX_BYTES                                   # Bytes de un par A_i + B_i (36)

matrix_t = uint16_t[MATRIX_SIZE][MATRIX_SIZE]
batch_t = uint16_t[NUM_PRODUCTS][MATRIX_SIZE][MATRIX_SIZE]      # K matrices
products_t = uint16_t[NUM_PRODUCTS]                             # Un elemento de cada una de las K C
ZERO_BATCH = [[[0] * MATRIX_SIZE for _ in range(MATRIX_SIZE)] for _ in range(NUM_PRODUCTS)]


# ------------------------------------------------------------------ tipos
@struct
class cell_t(NamedTuple):
    """Posicion (fila, columna) de un elemento dentro de una matriz."""
    row: uint3_t
    col: uint3_t


@enum
class mm_state_t(IntEnum):
    RECEIVE = 0     # Recibiendo los K pares (A_i, B_i) del PC
    COMPUTE = 1     # Calculando C_i = A_i * B_i en las K unidades a la vez
    SEND = 2        # Enviando las K matrices C_i al PC


uart_send, uart_tx_t = make_uart_tx(CLK_FREQ_MHZ, BAUD_RATE)
uart_recv, uart_rx_t = make_uart_rx(CLK_FREQ_MHZ, BAUD_RATE)

# ------------------------------------------------------------------ pines
rx_pin: Input[uint1_t]     # Pin RX de la FPGA (entrada serie desde el PC)
tx_pin: Output[uint1_t]    # Pin TX de la FPGA (salida serie hacia el PC)


# ------------------------------------------------------------------ logica combinacional
@hw_func
def dot_products(row: uint3_t, col: uint3_t, a: batch_t, b: batch_t) -> products_t:
    """Elemento [row][col] de C_u = A_u * B_u para las K unidades u a la vez (modulo 2^16).

    Se instancian K * MATRIX_SIZE productos independientes (2 DSP por unidad con N=3)."""
    result: products_t
    for u in range(NUM_PRODUCTS):
        acc: uint16_t = 0
        for k in range(MATRIX_SIZE):
            acc += a[u][row][k] * b[u][k][col]
        result[u] = acc
    return result


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


# ------------------------------------------------------------------ programa principal
@MAIN(CLK_FREQ_MHZ)
def main():
    # --- Maquina de estados y contadores
    state: Reg[mm_state_t]              # Fase actual (0=RECEIVE 1=COMPUTE 2=SEND)
    unit: Reg[uint4_t] = 0              # Matriz del lote que se recibe (RECEIVE) o se envia (SEND)
    byte_count: Reg[uint8_t] = 0        # Bytes recibidos del par actual / enviados de la C actual
    cell: Reg[cell_t] = cell_t(row=0, col=0)    # Elemento de la matriz que se escribe/calcula/lee ahora

    # --- Datos: K matrices de cada tipo
    matrix_a: Reg[batch_t] = ZERO_BATCH
    matrix_b: Reg[batch_t] = ZERO_BATCH
    matrix_c: Reg[batch_t] = ZERO_BATCH

    # --- Interfaz con el transmisor
    tx_data: Reg[uint8_t] = 0           # Byte a enviar
    tx_data_valid: Reg[uint1_t] = 0     # 1 = hay un byte listo en tx_data

    # --- Modulos UART (el receptor siempre esta listo para recibir: ready = 1)
    rx: uart_rx_t = uart_recv(rx_pin, 1)
    tx: uart_tx_t = uart_send(tx_data, tx_data_valid)

    # El TX acepta el byte en el ciclo en que tx_data_valid y tx.ready valen 1 a la vez. Se calcula
    # aqui, ANTES de asignar tx_data_valid, porque tras asignar un Reg su nombre ya vale el valor nuevo.
    tx_accepted: uint1_t = tx_data_valid & tx.ready

    # DEBUG (solo simulacion)
    if rx.rx_data_valid:
        sim_print(f"[RX ] byte={rx.rx_data} | state={uint2_t(state)} unit={unit} byte_count={byte_count} row={cell.row} col={cell.col}")
    if tx_data_valid:
        sim_print(f"[TX ] valid=1 tx_data={tx_data} tx_ready={tx.ready} | state={uint2_t(state)} unit={unit} byte_count={byte_count}")

    if state == mm_state_t.RECEIVE:
        if rx.rx_data_valid:
            # Los primeros MATRIX_BYTES bytes del par son A_unit y los siguientes B_unit
            if byte_count < MATRIX_BYTES:
                matrix_a[unit][cell.row][cell.col] = shift_in_byte(matrix_a[unit][cell.row][cell.col], rx.rx_data)
            else:
                matrix_b[unit][cell.row][cell.col] = shift_in_byte(matrix_b[unit][cell.row][cell.col], rx.rx_data)
            byte_count += 1
            # Elemento completo (todos sus bytes recibidos): pasa al siguiente
            if byte_count % BYTES_PER_ELEMENT == 0:
                cell = next_cell(cell)
            if byte_count == MATRIX_BYTES:
                # A_unit completa: B_unit empieza otra vez en el elemento [0][0]
                cell = cell_t(row=0, col=0)
            elif byte_count == PAIR_BYTES:
                # Par completo: el siguiente par va a la unidad siguiente
                sim_print(f"[MEM] par {unit} completo")
                byte_count = 0
                cell = cell_t(row=0, col=0)
                if unit == NUM_PRODUCTS - 1:
                    sim_print("[FSM] RECEIVE -> COMPUTE")
                    unit = 0
                    state = mm_state_t.COMPUTE
                else:
                    unit += 1
    elif state == mm_state_t.COMPUTE:
        # Un elemento de cada C_u por ciclo, en las K unidades a la vez, recorriendo con `cell`
        c_elements: products_t = dot_products(cell.row, cell.col, matrix_a, matrix_b)
        for u in range(NUM_PRODUCTS):
            matrix_c[u][cell.row][cell.col] = c_elements[u]
        sim_print(f"[COMPUTE] elemento [{cell.row}][{cell.col}] de las {NUM_PRODUCTS} C")
        if cell.row == MATRIX_SIZE - 1 and cell.col == MATRIX_SIZE - 1:
            sim_print("[FSM] COMPUTE -> SEND")
            cell = cell_t(row=0, col=0)
            byte_count = 0
            unit = 0
            state = mm_state_t.SEND
        else:
            cell = next_cell(cell)
    elif state == mm_state_t.SEND:
        # Bytes pares = byte bajo del elemento, impares = byte alto
        send_high_byte: uint1_t = (byte_count % BYTES_PER_ELEMENT == 1)
        tx_data = select_byte(matrix_c[unit][cell.row][cell.col], send_high_byte)
        tx_data_valid = 1
        if tx_accepted:
            sim_print(f"[SEND] C_{unit} byte {byte_count} aceptado por el TX (tx_data={tx_data})")
            byte_count += 1
            if byte_count % BYTES_PER_ELEMENT == 0:
                cell = next_cell(cell)
            if byte_count == MATRIX_BYTES:
                # C_unit completa entregada al TX
                byte_count = 0
                cell = cell_t(row=0, col=0)
                if unit == NUM_PRODUCTS - 1:
                    # Todo el lote entregado; el ultimo byte termina de salir por si solo
                    sim_print("[FSM] SEND -> RECEIVE")
                    unit = 0
                    tx_data_valid = 0   # Nada mas que enviar: el TX no debe repetir el ultimo byte
                    state = mm_state_t.RECEIVE
                else:
                    unit += 1

    tx_pin = tx.tx_out
