"""Money rounding (ТЗ § 17.1.7)."""

from decimal import ROUND_HALF_UP, Decimal


def round_money(amount: Decimal, minor_units: int) -> Decimal:
    """Nearest value; a half goes away from zero (§ 17.1.7)."""
    return amount.quantize(Decimal(1).scaleb(-minor_units), rounding=ROUND_HALF_UP)
