import itertools
import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from car_advisor.engine import copy_he
from car_advisor.engine.comparison import compare
from car_advisor.engine.explanation import BreakdownItem, build_explanation
from car_advisor.engine.types import Alternative, EmployeeProfile, Powertrain, RepParticipation
from tests.engine.test_comparison import (
    CURRENT_CAR,
    EV_CAR,
    EV_KEY,
    FAMILY_CAR,
    FAMILY_KEY,
    HIGHER_CAR,
    NO_CAR_PROFILE,
    PROFILE,
    RECORDS,
    REP_400,
    SPARE_CAR,
    WAIVER,
    make_inputs,
    make_record,
)

D = Decimal

# The wording comes from the copy, so these tests pin the layout and which phrase goes where, and
# leave the choice of words to copy_he.py.
USAGE = BreakdownItem.USAGE_VALUE
EV = BreakdownItem.EV_REFUND
PARTICIPATION = BreakdownItem.PARTICIPATION
MAINTENANCE = BreakdownItem.CAR_MAINTENANCE
PHRASES = {
    USAGE: copy_he.USAGE_VALUE_PHRASES,
    EV: copy_he.EV_REFUND_PHRASES,
    PARTICIPATION: copy_he.PARTICIPATION_PHRASES,
    MAINTENANCE: copy_he.CAR_MAINTENANCE_PHRASES,
}
AND = copy_he.EXPLANATION_AND
SIX_DIGITS = 999_999

# Transport_Rep has answered the participation question for the electric car only.
INPUTS = make_inputs(rep_participations={EV_KEY: REP_400, FAMILY_KEY: REP_400})


def up(amount: int) -> str:
    return copy_he.EXPLANATION_HEADLINE_UP.format(a=f"{amount:,}")


def down(amount: int) -> str:
    return copy_he.EXPLANATION_HEADLINE_DOWN.format(a=f"{amount:,}")


def phrase(item: BreakdownItem, shown: int) -> str:
    phrases = PHRASES[item]
    return (phrases.raises if shown > 0 else phrases.lowers).format(a=f"{abs(shown):,}")


def amounts(lines: tuple[str, ...]) -> list[int]:
    numbers = re.findall(r"₪(\d[\d,]*)", " ".join(lines))
    return [int(number.replace(",", "")) for number in numbers]


def six_digit_cases() -> Iterator[tuple[int, list[tuple[BreakdownItem, int]]]]:
    for difference in (SIX_DIGITS, -SIX_DIGITS, 0):
        for signs in itertools.product((1, -1), repeat=len(BreakdownItem)):
            components = [
                (item, sign * SIX_DIGITS) for item, sign in zip(BreakdownItem, signs, strict=True)
            ]
            yield difference, components


@pytest.mark.parametrize(
    ("difference", "template", "text"),
    [
        pytest.param(210, copy_he.EXPLANATION_HEADLINE_UP, "210", id="up"),
        pytest.param(-1234, copy_he.EXPLANATION_HEADLINE_DOWN, "1,234", id="down"),
        pytest.param(0, copy_he.EXPLANATION_HEADLINE_UNCHANGED, "0", id="unchanged"),
    ],
)
def test_the_headline_gives_the_direction_and_the_size_of_the_shown_difference(
    difference: int, template: str, text: str
) -> None:
    assert build_explanation(difference, []) == (f"{template.format(a=text)}.",)


@pytest.mark.parametrize("item", BreakdownItem)
@pytest.mark.parametrize("shown", [310, -310], ids=["raises", "lowers"])
def test_one_component_is_worded_by_the_direction_in_which_it_moves_net_pay(
    item: BreakdownItem, shown: int
) -> None:
    headline = up(310) if shown > 0 else down(310)

    assert build_explanation(shown, [(item, shown)]) == (f"{headline}: {phrase(item, shown)}.",)


def test_every_headline_and_phrase_has_its_own_wording() -> None:
    headlines = [
        copy_he.EXPLANATION_HEADLINE_UP,
        copy_he.EXPLANATION_HEADLINE_DOWN,
        copy_he.EXPLANATION_HEADLINE_UNCHANGED,
    ]
    phrases = [text for pair in PHRASES.values() for text in (pair.raises, pair.lowers)]

    assert len({*headlines, *phrases}) == len(headlines) + len(phrases)


def test_the_requirement_example_has_its_structure_in_hebrew() -> None:
    # English meaning: "Net pay goes up by ₪210 a month: the lower usage value saves ₪310, the EV
    # refund adds ₪250, and a higher participation takes off ₪350." The sentence goes on in the
    # second line, and the last component is joined with "and".
    components = [(USAGE, 310), (EV, 250), (PARTICIPATION, -350), (MAINTENANCE, 0)]

    assert build_explanation(210, components) == (
        f"{up(210)}: {phrase(USAGE, 310)}, {phrase(EV, 250)},",
        f"{AND}{phrase(PARTICIPATION, -350)}.",
    )


def test_the_first_two_components_go_in_line_1_and_the_rest_in_line_2() -> None:
    components = [(USAGE, 160), (EV, -250), (PARTICIPATION, 50), (MAINTENANCE, 900)]
    first, second, third, fourth = (phrase(item, shown) for item, shown in components)

    assert build_explanation(1, components[:1]) == (f"{up(1)}: {first}.",)
    assert build_explanation(1, components[:2]) == (f"{up(1)}: {first}, {AND}{second}.",)
    assert build_explanation(1, components) == (
        f"{up(1)}: {first}, {second},",
        f"{third}, {AND}{fourth}.",
    )


def test_a_component_of_zero_is_left_out_and_does_not_count_for_line_1() -> None:
    components = [(USAGE, 0), (EV, 250), (PARTICIPATION, -350), (MAINTENANCE, 900)]

    assert build_explanation(800, components) == (
        f"{up(800)}: {phrase(EV, 250)}, {phrase(PARTICIPATION, -350)},",
        f"{AND}{phrase(MAINTENANCE, 900)}.",
    )


def test_the_components_come_out_in_the_order_of_the_requirement_whatever_the_order_given() -> None:
    components = [(USAGE, 160), (EV, 250), (PARTICIPATION, -50), (MAINTENANCE, 900)]

    assert build_explanation(1260, components[::-1]) == build_explanation(1260, components)


def test_without_a_non_zero_component_the_explanation_is_a_single_line() -> None:
    zeros = [(item, 0) for item in BreakdownItem]

    assert build_explanation(0, zeros) == (f"{copy_he.EXPLANATION_HEADLINE_UNCHANGED}.",)
    assert build_explanation(210, zeros) == (f"{up(210)}.",)


def test_four_non_zero_six_digit_components_keep_each_line_within_120_characters() -> None:
    for difference, components in six_digit_cases():
        lines = build_explanation(difference, components)

        assert len(lines) == 2
        assert all(len(line) <= 120 for line in lines), lines


def test_the_text_has_no_latin_letters_or_underscores() -> None:
    for difference, components in six_digit_cases():
        lines = build_explanation(difference, components)

        assert not any(re.search(r"[A-Za-z_]", line) for line in lines), lines


@pytest.mark.parametrize(
    ("profile", "alternative"),
    [
        pytest.param(PROFILE, EV_CAR, id="electric-car"),
        pytest.param(PROFILE, WAIVER, id="giving-up-the-car"),
        pytest.param(NO_CAR_PROFILE, EV_CAR, id="from-no-car-to-the-electric-car"),
        pytest.param(PROFILE, FAMILY_CAR, id="family-track-car"),
        pytest.param(PROFILE, CURRENT_CAR, id="current-car"),
    ],
)
def test_a_calculated_alternative_explains_its_shown_difference_and_shown_components(
    profile: EmployeeProfile, alternative: Alternative
) -> None:
    result = compare(profile, [alternative], INPUTS).alternatives[0]

    assert result.status == "calculated"
    assert result.difference is not None
    assert result.breakdown is not None
    assert result.explanation is not None
    breakdown = result.breakdown
    shown = [
        result.difference.shown,
        breakdown.usage_value.shown,
        breakdown.ev_refund.shown,
        breakdown.participation.shown,
        breakdown.car_maintenance.shown,
    ]
    assert amounts(result.explanation) == [abs(value) for value in shown if value != 0]
    assert 1 <= len(result.explanation) <= 2
    assert all(len(line) <= 120 for line in result.explanation)


def test_the_electric_switch_is_explained_with_its_hand_checked_figures() -> None:
    # The same switch as in test_breakdown.py: the difference is +360, made of the usage value
    # +160, the EV refund +250, the participation -50 and a car maintenance of 0.
    result = compare(PROFILE, [EV_CAR], INPUTS).alternatives[0]

    assert result.explanation == (
        f"{up(360)}: {phrase(USAGE, 160)}, {phrase(EV, 250)},",
        f"{AND}{phrase(PARTICIPATION, -50)}.",
    )


def test_staying_with_the_current_car_is_explained_as_unchanged() -> None:
    result = compare(PROFILE, [CURRENT_CAR], INPUTS).alternatives[0]

    assert result.explanation == (f"{copy_he.EXPLANATION_HEADLINE_UNCHANGED}.",)


def test_the_text_uses_the_shown_amounts_and_skips_a_component_that_shows_as_zero() -> None:
    # In exact amounts the usage value part is 0.32 and the participation part is -0.5, and the
    # difference is 249.82. The parts show as 0 and 0, and the difference as 250, so the text
    # names only the EV refund of 250, as the table does (see test_breakdown.py).
    answer = RepParticipation(amount=D("350.5"), effective_date=date(2026, 1, 1))
    inputs = make_inputs(
        records={**RECORDS, EV_KEY: make_record(EV_KEY, Powertrain.ELECTRIC, 1999)},
        rep_participations={EV_KEY: answer},
    )

    result = compare(PROFILE, [EV_CAR], inputs).alternatives[0]

    assert result.explanation == (f"{up(250)}: {phrase(EV, 250)}.",)


def test_only_a_calculated_alternative_has_an_explanation() -> None:
    results = compare(PROFILE, [EV_CAR, HIGHER_CAR, SPARE_CAR, WAIVER], INPUTS).alternatives

    assert [(result.status, result.explanation is not None) for result in results] == [
        ("calculated", True),
        ("not_eligible", False),
        ("pending", False),
        ("calculated", True),
    ]


def test_no_alternative_has_an_explanation_while_today_is_pending() -> None:
    profile = PROFILE.model_copy(update={"current_car_participation": None})

    comparison = compare(profile, [EV_CAR, WAIVER, HIGHER_CAR], INPUTS)

    assert comparison.today.status == "pending"
    assert [result.status for result in comparison.alternatives] == [
        "pending",
        "pending",
        "not_eligible",
    ]
    assert all(result.explanation is None for result in comparison.alternatives)
