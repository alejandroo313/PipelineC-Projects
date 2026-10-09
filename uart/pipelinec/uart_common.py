DEFAULT_BAUD = 115200
DATA_BITS = 8


def clk_per_bit(clk_mhz: float, baud: int = DEFAULT_BAUD) -> int:
    """Ciclos de reloj por periodo de bit (p. ej. 27 MHz / 115200 = 234)."""
    return int(clk_mhz * 1_000_000 / baud)
