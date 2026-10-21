from dataclasses import fields
from datetime import date
from decimal import Decimal

import pytest

from car_advisor.engine.comparison import (
    AlternativeResult,
    Breakdown,
    ComparisonInputs,
    TodayResult,
    compare,
)
from car_advisor.engine.money import Amount, display
from car_advisor.engine.types import Alternative, EmployeeProfile, Powertrain, RepParticipation
from tests.engine.test_comparison import (
    CURRENT_CAR,
    CURRENT_KEY,
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
    SPARE_KEY,
    UNLISTED_CAR,
    WAIVER,
    WAREHOUSE_NO_CAR,
    make_inputs,
    make_record,
    make_rules,
)

D = Decimal

# The cars, the profile and the payroll rules are those of test_comparison.py. Every expected
# value below is worked out by hand from those rules: tax of 10% up to 10,000, 20% up to 20,000
# and 30% up to 40,000, insurance of 1% up to 6,000 and 7% above, health tax of 3.5% and 5%
# likewise, and for the profile credits of 450 + 245 = 695 and a pension of 720. Between 10,000
# and 20,000 of taxable income, a shekel of usage value or maintenance therefore costs
# 20% + 7% + 5% = 32%.

# Transport_Rep has answered the participation question for the electric car and the family car.
ANSWERED = make_inputs(rep_participations={EV_KEY: REP_400, FAMILY_KEY: REP_400})


def whole(value: int) -> Amount:
    return Amount(exact=D(value), shown=value)


def parts(breakdown: Breakdown) -> list[Amount]:
    return [
        breakdown.usage_value,
        breakdown.ev_refund,
        breakdown.participation,
        breakdown.car_maintenance,
    ]


def switch(
    alternative: Alternative,
    *,
    profile: EmployeeProfile = PROFILE,
    inputs: ComparisonInputs = ANSWERED,
) -> tuple[TodayResult, AlternativeResult]:
    comparison = compare(profile, [alternative], inputs)
    return comparison.today, comparison.alternatives[0]


def assert_adds_up(
    today: TodayResult, result: AlternativeResult, *, within_a_shekel: bool = True
) -> None:
    assert today.net_pay is not None
    assert result.net_pay is not None
    assert result.difference is not None
    assert result.breakdown is not None
    pieces = parts(result.breakdown)
    assert sum((piece.exact for piece in pieces), start=D(0)) == result.difference.exact
    assert sum(piece.shown for piece in pieces) == result.difference.shown
    assert result.difference.exact == result.net_pay.exact - today.net_pay.exact
    assert result.difference.shown == result.net_pay.shown - today.net_pay.shown
    if within_a_shekel:
        assert all(abs(piece.shown - piece.exact) < 1 for piece in pieces)


def test_a_switch_to_an_electric_car_is_split_into_the_hand_checked_components() -> None:
    today, result = switch(EV_CAR)

    # Today nets 8,295 and the electric car (participation 400) nets 8,655. In between:
    # - usage value 2,000 -> 1,500: 500 less taxable income saves 32% of 500 = 160, so 8,455;
    # - EV refund 0 -> 250 is added to the net pay untaxed: 8,705;
    # - participation 350 -> 400 is deducted from the net pay: 8,655;
    # - car maintenance stays 0: 8,655.
    assert today.net_pay == whole(8295)
    assert result.net_pay == whole(8655)
    assert result.breakdown == Breakdown(
        usage_value=whole(160),
        ev_refund=whole(250),
        participation=whole(-50),
        car_maintenance=whole(0),
    )
    assert result.difference == whole(360)  # 160 + 250 - 50 + 0 = 8,655 - 8,295
    assert_adds_up(today, result)


@pytest.mark.parametrize(
    ("profile", "alternative", "expected", "difference"),
    [
        # From 8,295. Without the car, 2,000 less taxable income saves 32% = 640 (8,935), the
        # refund stays 0, and the participation of 350 is no longer deducted (9,285). The
        # maintenance of 1,500 is taxed 32% and is outside the contribution base, so it adds
        # 1,500 - 480 = 1,020 (10,305).
        pytest.param(PROFILE, WAIVER, (640, 0, 350, 1020), 2010, id="giving-up-the-car"),
        # From 9,285 without a car and without a maintenance. 1,500 of usage value costs 32% = 480
        # (8,805), the refund adds 250 (9,055) and the participation of 400 is deducted (8,655).
        pytest.param(
            WAREHOUSE_NO_CAR,
            EV_CAR,
            (-480, 250, -400, 0),
            -630,
            id="from-no-car-to-the-electric-car",
        ),
        # From 10,305 with the maintenance of 1,500 that a waiver pays. The same three steps give
        # 9,825, 10,075 and 9,675. Losing the maintenance takes 1,500 - 480 = 1,020 (8,655).
        pytest.param(
            NO_CAR_PROFILE,
            EV_CAR,
            (-480, 250, -400, -1020),
            -1650,
            id="from-a-waiver-to-the-electric-car",
        ),
        # From 8,295. A usage value of 2,500 costs 32% of the 500 more = 160 (8,135). In group C
        # the gap is 6,000 - 4,500 = 1,500 and the employee pays half, 750, on top of the
        # participation of 400, so the deduction grows from 350 to 1,150 (7,335).
        pytest.param(PROFILE, FAMILY_CAR, (-160, 0, -800, 0), -960, id="family-track-car"),
    ],
)
def test_other_switches_are_split_into_their_hand_checked_components(
    profile: EmployeeProfile,
    alternative: Alternative,
    expected: tuple[int, int, int, int],
    difference: int,
) -> None:
    today, result = switch(alternative, profile=profile)

    assert result.status == "calculated"
    assert result.breakdown is not None
    assert parts(result.breakdown) == [whole(part) for part in expected]
    assert result.difference == whole(difference)
    assert_adds_up(today, result)


def test_the_components_are_named_after_the_four_items_in_the_order_of_the_requirement() -> None:
    assert [field.name for field in fields(Breakdown)] == [
        "usage_value",
        "ev_refund",
        "participation",
        "car_maintenance",
    ]


def test_staying_with_the_current_car_changes_nothing_in_any_component() -> None:
    today, result = switch(CURRENT_CAR)

    assert result.breakdown == Breakdown(
        usage_value=whole(0),
        ev_refund=whole(0),
        participation=whole(0),
        car_maintenance=whole(0),
    )
    assert result.difference == whole(0)
    assert_adds_up(today, result)


def test_the_participation_step_sees_the_usage_value_that_the_first_step_already_changed() -> None:
    # With the flag on, a participation reduces the taxable usage value, but never below 0.
    # Today taxes 2,000 - 350 = 1,650: tax 1,000 + 730 - 695 = 1,035, insurance 60 + 535.5,
    # health tax 210 + 382.5, so the net pay is 12,000 - 1,035 - 595.5 - 592.5 - 720 - 300 - 350
    # = 8,407. The alternative has a usage value of 300 and a participation of 400.
    # - usage value 2,000 -> 300 with the participation still 350: nothing is left to tax
    #   (300 - 350 < 0), so the taxable income falls by 1,650 and 32% of it, 528, is saved: 8,935;
    # - EV refund stays 0: 8,935;
    # - participation 350 -> 400 reduces a usage value that is already 0, so all 50 comes off the
    #   net pay: 8,885. Taken before the usage value, it would cost only 34, as 16 of tax is saved.
    records = {**RECORDS, SPARE_KEY: make_record(SPARE_KEY, Powertrain.HYBRID, 300)}
    inputs = make_inputs(
        rules=make_rules(participation_reduces_usage_value=True),
        records=records,
        rep_participations={SPARE_KEY: REP_400},
    )

    today, result = switch(SPARE_CAR, inputs=inputs)

    assert today.net_pay == whole(8407)
    assert result.breakdown == Breakdown(
        usage_value=whole(528),
        ev_refund=whole(0),
        participation=whole(-50),
        car_maintenance=whole(0),
    )
    assert result.difference == whole(478)  # 8,885 - 8,407
    assert_adds_up(today, result)


def test_components_depend_on_the_steps_before_them_across_a_bracket_limit() -> None:
    # A base salary of 19,000 without a study fund puts the 20,000 limit of the 20% bracket
    # inside the steps. The pension is 1,140 and the credits are 695 in every step. The refund is
    # taxable gross pay in this mode, and Today's electric car has a usage value of 2,000.
    # Tax is 1,000 + 20% of the income from 10,000 to 20,000 + 30% above 20,000, less 695;
    # insurance is 60 + 7% of the income above 6,000, and health tax is 210 + 5% of it.
    #
    #   step                gross    taxable   tax    insurance  health  participation  net pay
    #   Today               19,250   21,250    2,680  1,127.5    972.5   350            12,980
    #   usage value 0       19,250   19,250    2,155    987.5    872.5   350            13,745
    #   EV refund 0         19,000   19,000    2,105    970      860     350            13,575
    #   participation 0     19,000   19,000    2,105    970      860       0            13,925
    #   maintenance 1,500   20,500   20,500    2,455  1,075      935       0            14,895
    #   (net pay = gross - tax - insurance - health - pension 1,140 - participation)
    #
    # In another order the same switch splits differently, for example 870 for the maintenance
    # when it is added first, or -145 for the refund and 740 for the usage value when the refund
    # is removed first.
    profile = PROFILE.model_copy(update={"base_salary": D(19000), "study_fund_rate_percent": D(0)})
    records = {**RECORDS, CURRENT_KEY: make_record(CURRENT_KEY, Powertrain.ELECTRIC, 2000)}
    inputs = make_inputs(rules=make_rules(ev_mode="taxable_gross"), records=records)

    today, result = switch(WAIVER, profile=profile, inputs=inputs)

    assert today.net_pay == whole(12980)
    assert result.net_pay == whole(14895)
    assert result.breakdown == Breakdown(
        usage_value=whole(765),
        ev_refund=whole(-170),
        participation=whole(350),
        car_maintenance=whole(970),
    )
    assert result.difference == whole(1915)  # 765 - 170 + 350 + 970 = 14,895 - 12,980
    assert_adds_up(today, result)


@pytest.mark.parametrize(
    ("today_participation", "usage_value", "participation", "expected", "difference"),
    [
        # Today nets 8,295. A usage value of 1,999 saves 32% of 1 = 0.32: 8,295.32. The refund
        # adds 250: 8,545.32. A participation of 350.5 takes off 0.5: 8,544.82. These show as
        # 8,295, 8,295, 8,545, 8,545 and 8,545, so the shown parts are 0, 250, 0 and 0.
        # Rounding each exact part would give 0, 250, -1 (half away from zero) and 0 = 249.
        pytest.param(
            "350",
            1999,
            "350.5",
            [Amount(D("0.32"), 0), whole(250), Amount(D("-0.5"), 0), whole(0)],
            Amount(D("249.82"), 250),
            id="a-part-of-exactly-half-a-shekel",
        ),
        # Today with a participation of 350.5 nets 8,294.5, which shows as 8,295. A usage value
        # of 1,998 saves 32% of 2 = 0.64: 8,295.14. The refund: 8,545.14. A participation of
        # 349.9 gives back 0.6: 8,545.74. These show as 8,295, 8,295, 8,545 and 8,546, so the
        # shown parts are 0, 250, 1 and 0. Rounding each exact part would give 1 + 250 + 1 = 252.
        pytest.param(
            "350.5",
            1998,
            "349.9",
            [Amount(D("0.64"), 0), whole(250), Amount(D("0.6"), 1), whole(0)],
            Amount(D("251.24"), 251),
            id="a-net-pay-of-exactly-half-a-shekel",
        ),
    ],
)
def test_the_shown_parts_add_up_where_rounding_each_exact_part_would_not(
    today_participation: str,
    usage_value: int,
    participation: str,
    expected: list[Amount],
    difference: Amount,
) -> None:
    profile = PROFILE.model_copy(update={"current_car_participation": D(today_participation)})
    answer = RepParticipation(amount=D(participation), effective_date=date(2026, 1, 1))
    inputs = make_inputs(
        records={**RECORDS, EV_KEY: make_record(EV_KEY, Powertrain.ELECTRIC, usage_value)},
        rep_participations={EV_KEY: answer},
    )

    today, result = switch(EV_CAR, profile=profile, inputs=inputs)

    assert result.breakdown is not None
    assert parts(result.breakdown) == expected
    assert result.difference == difference
    assert_adds_up(today, result)
    assert sum(display(piece.exact) for piece in expected) != difference.shown


@pytest.mark.parametrize(
    ("profile", "alternative", "status"),
    [
        pytest.param(PROFILE, HIGHER_CAR, "not_eligible", id="not-eligible"),
        pytest.param(PROFILE, SPARE_CAR, "pending", id="participation-in-the-new-car-unknown"),
        pytest.param(PROFILE, UNLISTED_CAR, "pending", id="car-outside-the-catalog"),
        pytest.param(
            PROFILE.model_copy(update={"occupation": None}),
            WAIVER,
            "pending",
            id="occupation-unknown",
        ),
        pytest.param(
            PROFILE.model_copy(update={"current_car_participation": None}),
            EV_CAR,
            "pending",
            id="today-pending",
        ),
    ],
)
def test_only_a_calculated_alternative_has_a_breakdown(
    profile: EmployeeProfile, alternative: Alternative, status: str
) -> None:
    _, result = switch(alternative, profile=profile)

    assert result.status == status
    assert result.breakdown is None
    assert result.difference is None


def test_the_parts_add_up_when_a_net_pay_changes_sign_between_two_half_shekel_values() -> None:
    # Without a salary the net pay is the refund less the participation. Today, with a
    # participation of 0.5, nets -0.5. The refund of 250 makes it 249.5 and the new participation
    # of 250.5 brings it back to -0.5. These show as -1, -1, 250 and -1 (half away from zero), so
    # the refund shows as 251 and the participation as -251, although they are exactly 250 and
    # -250. This is the one case in which a shown part is a whole shekel from its exact part
    # (design, Open Questions): the add-up comes first, and the parts add up to the difference 0.
    profile = PROFILE.model_copy(
        update={
            "base_salary": D(0),
            "pension_rate_percent": D(0),
            "study_fund_rate_percent": D(0),
            "credit_points": D(0),
            "current_car_participation": D("0.5"),
        }
    )
    answer = RepParticipation(amount=D("250.5"), effective_date=date(2026, 1, 1))
    records = {
        **RECORDS,
        CURRENT_KEY: make_record(CURRENT_KEY, Powertrain.CONVENTIONAL, 0),
        EV_KEY: make_record(EV_KEY, Powertrain.ELECTRIC, 0),
    }
    inputs = make_inputs(records=records, rep_participations={EV_KEY: answer})

    today, result = switch(EV_CAR, profile=profile, inputs=inputs)

    assert today.net_pay == Amount(D("-0.5"), -1)
    assert result.breakdown == Breakdown(
        usage_value=whole(0),
        ev_refund=Amount(D(250), 251),
        participation=Amount(D(-250), -251),
        car_maintenance=whole(0),
    )
    assert result.difference == whole(0)
    assert_adds_up(today, result, within_a_shekel=False)
