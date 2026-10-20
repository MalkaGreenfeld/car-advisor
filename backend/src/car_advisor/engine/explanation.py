from collections.abc import Mapping, Sequence
from enum import StrEnum

from car_advisor.engine import copy_he


class BreakdownItem(StrEnum):
    """The car items that make up the difference from Today, declared in the order of Req 8.6."""

    USAGE_VALUE = "usage_value"
    EV_REFUND = "ev_refund"
    PARTICIPATION = "participation"
    CAR_MAINTENANCE = "car_maintenance"


_ORDER = tuple(BreakdownItem)

_PHRASES: Mapping[BreakdownItem, copy_he.ComponentPhrases] = {
    BreakdownItem.USAGE_VALUE: copy_he.USAGE_VALUE_PHRASES,
    BreakdownItem.EV_REFUND: copy_he.EV_REFUND_PHRASES,
    BreakdownItem.PARTICIPATION: copy_he.PARTICIPATION_PHRASES,
    BreakdownItem.CAR_MAINTENANCE: copy_he.CAR_MAINTENANCE_PHRASES,
}

# The longest headline and two of the longest phrases fit in 120 characters (Req 8.9).
_FIRST_LINE_PHRASES = 2


def build_explanation(
    difference_shown: int, components: Sequence[tuple[BreakdownItem, int]]
) -> tuple[str, ...]:
    """Word the shown difference and its non-zero shown components as one sentence (Req 8.9).

    The first line holds the headline and the first two components, the second line the rest.
    Every amount is the absolute value of a shown amount, so each number of the text is a number
    of the comparison table (Req 8.10). The components come out in the order of Req 8.6 whatever
    order they are given in.
    """
    ordered = sorted(components, key=lambda component: _ORDER.index(component[0]))
    phrases = [_phrase(item, shown) for item, shown in ordered if shown != 0]
    if len(phrases) > 1:
        phrases[-1] = copy_he.EXPLANATION_AND + phrases[-1]

    headline = _headline(difference_shown)
    if not phrases:
        return (f"{headline}.",)

    first_line = ", ".join(phrases[:_FIRST_LINE_PHRASES])
    rest = phrases[_FIRST_LINE_PHRASES:]
    if not rest:
        return (f"{headline}: {first_line}.",)
    # The sentence continues in the second line, which is why the first one ends with a comma.
    return (f"{headline}: {first_line},", f"{', '.join(rest)}.")


def _headline(difference_shown: int) -> str:
    if difference_shown > 0:
        template = copy_he.EXPLANATION_HEADLINE_UP
    elif difference_shown < 0:
        template = copy_he.EXPLANATION_HEADLINE_DOWN
    else:
        template = copy_he.EXPLANATION_HEADLINE_UNCHANGED
    return template.format(a=_amount(difference_shown))


def _phrase(item: BreakdownItem, shown: int) -> str:
    phrases = _PHRASES[item]
    template = phrases.raises if shown > 0 else phrases.lowers
    return template.format(a=_amount(shown))


def _amount(shown: int) -> str:
    return f"{abs(shown):,}"
