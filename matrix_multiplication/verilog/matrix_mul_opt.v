// Multiplicador de matrices NxN (uint16) por UART para la Tang Nano 20K.
// VERSION OPTIMIZADA: COMPUTE calcula UN elemento de C por ciclo (N productos en paralelo),
// y tarda N*N ciclos. Equivalente en Verilog a ../matrix_multiplication_optimized.py (PipelineC).
//
// Protocolo (8N1), elementos uint16 en little-endian (byte bajo primero), fila a fila:
//     PC -> FPGA : matriz A (N*N*2 bytes) + matriz B (N*N*2 bytes)
//     FPGA -> PC : matriz C = A * B (N*N*2 bytes)
//
// Flujo (maquina de estados):
//     S_RECEIVE --(2*N*N*2 bytes)--> S_COMPUTE --(N*N ciclos)--> S_SEND --(N*N*2 bytes)--> S_RECEIVE
//
// Las matrices son vectores empaquetados (registros, no RAM): el elemento (i,j) ocupa
// los bits [(i*N+j)*16 +: 16]. Se usan los modulos uart_rx / uart_tx tal cual (../roadmap/uart/verilog).

module matrix_mul_opt #(
	parameter CLK_FRE     = 27,       // frecuencia del reloj (MHz)
	parameter BAUD_RATE   = 1000000,  // baudios (el PC debe usar los mismos)
	parameter MATRIX_SIZE = 3         // N
)(
	input  clk,
	input  rx_pin,                    // entrada serie desde el PC
	output tx_pin                     // salida serie hacia el PC
);

// ------------------------------------------------------------------ constantes
function integer clog2;               // ceil(log2(value)), para dimensionar contadores
	input integer value;
	integer v;
	begin
		v = value - 1;
		for (clog2 = 0; v > 0; clog2 = clog2 + 1)
			v = v >> 1;
	end
endfunction

localparam BYTES_PER_ELEMENT = 2;                                   // elementos uint16
localparam NUM_ELEMENTS      = MATRIX_SIZE * MATRIX_SIZE;
localparam MATRIX_BYTES      = NUM_ELEMENTS * BYTES_PER_ELEMENT;    // bytes de UNA matriz
localparam INPUT_BYTES       = 2 * MATRIX_BYTES;                    // bytes de A + B
localparam OUTPUT_BYTES      = MATRIX_BYTES;                        // bytes de C
localparam IDX_W             = clog2(MATRIX_SIZE + 1);              // fila/columna (llegan a N)
localparam CNT_W             = clog2(INPUT_BYTES + 1);              // contador de bytes

localparam S_RECEIVE = 2'd0;          // recibiendo A y B
localparam S_COMPUTE = 2'd1;          // calculando C = A * B
localparam S_SEND    = 2'd2;          // enviando C

// ------------------------------------------------------------------ reset de arranque
// Sin pin de reset externo (igual que el diseno de PipelineC): un contador genera rst_n
// unos ciclos despues de cargar la FPGA.
reg [7:0] por_cnt = 8'd0;
wire      rst_n   = &por_cnt;
always @(posedge clk)
	if (!rst_n)
		por_cnt <= por_cnt + 8'd1;

// ------------------------------------------------------------------ UART
wire [7:0] rx_data;
wire       rx_data_valid;
wire       rx_data_ready = 1'b1;      // el receptor siempre esta listo
wire       tx_data_ready;
reg  [7:0] tx_data       = 8'd0;
reg        tx_data_valid = 1'b0;

uart_rx #(.CLK_FRE(CLK_FRE), .BAUD_RATE(BAUD_RATE)) uart_rx_inst (
	.clk           (clk),
	.rst_n         (rst_n),
	.rx_data       (rx_data),
	.rx_data_valid (rx_data_valid),
	.rx_data_ready (rx_data_ready),
	.rx_pin        (rx_pin)
);

uart_tx #(.CLK_FRE(CLK_FRE), .BAUD_RATE(BAUD_RATE)) uart_tx_inst (
	.clk           (clk),
	.rst_n         (rst_n),
	.tx_data       (tx_data),
	.tx_data_valid (tx_data_valid),
	.tx_data_ready (tx_data_ready),
	.tx_pin        (tx_pin)
);

// ------------------------------------------------------------------ estado
reg [1:0]       state      = S_RECEIVE;
reg [CNT_W-1:0] byte_count = {CNT_W{1'b0}};   // bytes recibidos (RECEIVE) o enviados (SEND)
reg [IDX_W-1:0] row        = {IDX_W{1'b0}};   // elemento que se escribe / lee ahora
reg [IDX_W-1:0] col        = {IDX_W{1'b0}};

reg [NUM_ELEMENTS*16-1:0] mat_a = {NUM_ELEMENTS*16{1'b0}};
reg [NUM_ELEMENTS*16-1:0] mat_b = {NUM_ELEMENTS*16{1'b0}};
reg [NUM_ELEMENTS*16-1:0] mat_c = {NUM_ELEMENTS*16{1'b0}};

// ------------------------------------------------------------------ logica combinacional
// Siguiente elemento recorriendo la matriz fila a fila (la ultima columna salta de fila)
wire [IDX_W-1:0] col_inc  = col + 1'b1;
wire             col_wrap = (col_inc == MATRIX_SIZE);
wire [IDX_W-1:0] next_col = col_wrap ? {IDX_W{1'b0}} : col_inc;
wire [IDX_W-1:0] next_row = col_wrap ? row + 1'b1    : row;

wire [31:0]      elem_idx = row * MATRIX_SIZE + col;      // indice lineal del elemento actual
wire [CNT_W-1:0] bc_next  = byte_count + 1'b1;            // byte_count tras este byte
wire             elem_end = ((bc_next % BYTES_PER_ELEMENT) == 0);   // elemento completo

// Elemento C[row][col] = fila `row` de A por columna `col` de B (aritmetica modulo 2^16).
// Los indices son de ejecucion: leer A y B genera multiplexores.
reg [15:0] dot;
integer    k;
always @* begin
	dot = 16'd0;
	for (k = 0; k < MATRIX_SIZE; k = k + 1)
		dot = dot + mat_a[(row*MATRIX_SIZE + k)*16 +: 16] * mat_b[(k*MATRIX_SIZE + col)*16 +: 16];
end

// ------------------------------------------------------------------ maquina de estados
always @(posedge clk) begin
	if (!rst_n) begin
		state         <= S_RECEIVE;
		byte_count    <= {CNT_W{1'b0}};
		row           <= {IDX_W{1'b0}};
		col           <= {IDX_W{1'b0}};
		tx_data_valid <= 1'b0;
	end else begin
		case (state)
			S_RECEIVE: begin
				if (rx_data_valid) begin
					// Los primeros MATRIX_BYTES bytes son A y los siguientes B. El byte nuevo entra por
					// la parte alta del elemento y el resto se desplaza: tras 2 bytes queda little-endian.
					if (byte_count < MATRIX_BYTES)
						mat_a[elem_idx*16 +: 16] <= {rx_data, mat_a[elem_idx*16 + 8 +: 8]};
					else
						mat_b[elem_idx*16 +: 16] <= {rx_data, mat_b[elem_idx*16 + 8 +: 8]};
					byte_count <= bc_next;
					if (elem_end) begin
						row <= next_row;
						col <= next_col;
					end
					if (bc_next == MATRIX_BYTES) begin
						// A completa: B empieza otra vez en el elemento [0][0]
						row <= {IDX_W{1'b0}};
						col <= {IDX_W{1'b0}};
					end else if (bc_next == INPUT_BYTES) begin
						row        <= {IDX_W{1'b0}};
						col        <= {IDX_W{1'b0}};
						byte_count <= {CNT_W{1'b0}};
						state      <= S_COMPUTE;
					end
				end
			end

			S_COMPUTE: begin
				// Un elemento de C por ciclo, recorriendo la matriz fila a fila
				mat_c[elem_idx*16 +: 16] <= dot;
				if (row == MATRIX_SIZE - 1 && col == MATRIX_SIZE - 1) begin
					row        <= {IDX_W{1'b0}};
					col        <= {IDX_W{1'b0}};
					byte_count <= {CNT_W{1'b0}};
					state      <= S_SEND;
				end else begin
					row <= next_row;
					col <= next_col;
				end
			end

			S_SEND: begin
				// Bytes pares = byte bajo del elemento, impares = byte alto
				tx_data       <= byte_count[0] ? mat_c[elem_idx*16 + 8 +: 8] : mat_c[elem_idx*16 +: 8];
				tx_data_valid <= 1'b1;
				// El TX acepta el byte en el ciclo en que tx_data_valid y tx_data_ready valen 1 a la vez
				if (tx_data_valid && tx_data_ready) begin
					byte_count <= bc_next;
					if (elem_end) begin
						row <= next_row;
						col <= next_col;
					end
					if (bc_next == OUTPUT_BYTES) begin
						// Todos los bytes entregados; el ultimo termina de salir por si solo
						byte_count    <= {CNT_W{1'b0}};
						row           <= {IDX_W{1'b0}};
						col           <= {IDX_W{1'b0}};
						tx_data_valid <= 1'b0;
						state         <= S_RECEIVE;
					end
				end
			end

			default: state <= S_RECEIVE;
		endcase
	end
end

endmodule
