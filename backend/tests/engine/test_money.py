from decimal import Decimal, localcontext

import pytest

from car_advisor.engine.money import Amount, amount, display, percent_of


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("1234.50", 1235),
        ("1234.49", 1234),
        ("-0.50", -1),
        ("-1234.50", -1235),
    ],
)
def test_display_rounds_half_away_from_zero(value: str, shown: int) -> None:
    result = display(Decimal(value))

    assert result == shown
    assert isinstance(result, int)


def test_display_does_not_depend_on_the_context_precision() -> None:
    assert display(Decimal("1" * 30 + ".5")) == int("1" * 29 + "2")
    with localcontext(prec=1):
        assert display(Decimal("999.5")) == 1000


def test_amount_keeps_the_exact_value_next_to_the_shown_one() -> None:
    assert amount(Decimal("1234.4999")) == Amount(exact=Decimal("1234.4999"), shown=1234)


@pytest.mark.parametrize(
    ("base", "rate_percent", "expected"),
    [
        ("16000", "6", "960"),
        ("1234.56", "7.5", "92.592"),
        ("12345.67", "2.345", "289.5059615"),
    ],
)
def test_percent_of_is_exact(base: str, rate_percent: str, expected: str) -> None:
    assert percent_of(Decimal(base), Decimal(rate_percent)) == Decimal(expected)
