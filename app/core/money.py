from decimal import Decimal, ROUND_HALF_UP
from typing import Union

MONEY_EXPONENT = Decimal("0.01")


def quantize_money(val: Union[Decimal, float, str, int]) -> Decimal:
    """Safely round financial figures using statutory half-up rounding to two decimal places."""
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return val.quantize(MONEY_EXPONENT, rounding=ROUND_HALF_UP)
