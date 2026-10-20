from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal


@dataclass(frozen=True)
class Amount:
    exact: Decimal
    shown: int


def display(value: Decimal) -> int:
    # quantize raises InvalidOperation when the result has more digits than the context
    # precision (28 by default), so a local context fits every whole digit plus a carry
    # such as 999.5 -> 1000.
    whole_digits = Context(prec=max(value.adjusted(), 0) + 2)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP, context=whole_digits))


def amount(value: Decimal) -> Amount:
    return Amount(exact=value, shown=display(value))


def percent_of(base: Decimal, rate_percent: Decimal) -> Decimal:
    return base * rate_percent / 100
