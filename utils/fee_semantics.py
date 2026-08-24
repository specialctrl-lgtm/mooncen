from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal


FeeStatus = Literal["UNKNOWN", "FREE", "PAID"]


def fee_status(value: Any) -> FeeStatus:
    """Classify a fee without collapsing an unpublished value into free."""

    if value is None or isinstance(value, bool):
        return "UNKNOWN"
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return "UNKNOWN"
    if not amount.is_finite() or amount < 0:
        return "UNKNOWN"
    return "FREE" if amount == 0 else "PAID"
