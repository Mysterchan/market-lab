"""Pure input conversion shared by CLI and desktop."""

from decimal import Decimal, InvalidOperation
from typing import Union

from .models import MONEY_SCALE, ContractError, integer


def money(text: Union[str, int, Decimal]) -> int:
    """Convert a decimal USD amount to integer ten-thousandths, rejecting finer precision."""
    try:
        amount = Decimal(text) * MONEY_SCALE
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise ValueError('amount must have at most four decimal places')
        result = int(amount)
        integer(result, 'amount')
        return result
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ContractError(str(exc)) from exc
