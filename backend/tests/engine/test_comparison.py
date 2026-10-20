import copy
from collections.abc import Mapping
from dataclasses import fields
from datetime import date
from decimal import Decimal
from typing import Any, Literal

import pytest

from car_advisor.engine.comparison import (
    AlternativeResult,
    ComparisonInputs,
    MissingItem,
    compare,
)
from car_advisor.engine.config import CarCatalog, OccupationsTable, RulesConfig
from car_advisor.engine.eligibility import check_eligibility
from car_advisor.engine.money import Amount, amount
from car_advisor.engine.payroll import PayrollInput, PayrollOk, calculate_net_pay
from car_advisor.engine.types import (
    Alternative,
    CarAlternative,
    CarKey,
    CarRecord,
    EmployeeProfile,
    Group,
    MissingField,
    PayComponent,
    Powertrain,
    RepParticipation,
    WaiverAlternative,
)

D = Decimal
SOURCE = "https://example.org/rates"


# The payroll rates are round numbers that keep the hand arithmetic short. They are not the 2026
# parameters. The group budgets, the Family_Track split and the EV refund are the challenge's.
def make_rules(
    *,
    employee_share_percent: int = 50,
    ev_mode: Literal["net", "taxable_gross"] = "net",
    participation_reduces_usage_value: bool = False,
) -> RulesConfig:
    return RulesConfig.model_validate(
        {
            "groups": {
                "order": ["A", "B", "C", "D"],
                "budgets": {"A": 3500, "B": 3700, "C": 4500, "D": 5200},
            },
            "family_track": {
                "children_threshold": 4,
                "employer_share_percent": 100 - employee_share_percent,
                "employee_share_percent": employee_share_percent,
            },
            "ev_refund": {"amount": 250, "mode": ev_mode},
            "unused_budget": "not_paid",
            "flags": {
                "participation_reduces_usage_value": participation_reduces_usage_value,
                "variable_pay_in_contribution_base": False,
                "car_maintenance_outside_benefits_base": True,
            },
            "payroll": {
                "net_pay_method": "monthly",
                "income_tax": {
                    "source": SOURCE,
                    "brackets": [
                        {"above": 0, "up_to": 10000, "rate_percent": 10},
                        {"above": 10000, "up_to": 20000, "rate_percent": 20},
                        {"above": 20000, "up_to": 40000, "rate_percent": 30},
                        {"above": 40000, "up_to": None, "rate_percent": 40},
                    ],
                },
                "credit_point": {"value": 200, "source": SOURCE},
                "social_insurance": {
                    "source": SOURCE,
                    "reduced_rate_threshold": 6000,
                    "max_insurable_income": 30000,
                    "national_insurance": {"reduced_rate_percent": 1, "full_rate_percent": 7},
                    "health_tax": {"reduced_rate_percent": 3.5, "full_rate_percent": 5},
                },
                "pension_credit": {
                    "source": SOURCE,
                    "rate_percent": 35,
                    "qualifying_share_percent": 7,
                    "income_ceiling": 10000,
                },
                "study_fund": {
                    "source": SOURCE,
                    "salary_ceiling": 16000,
                    "exempt_employer_rate_percent": 7.5,
                    "employer_rate_percent": 7.5,
                },
            },
            "pending": [
                {"field": "ev_refund.mode", "default": ev_mode, "question": "Net or gross-up?"},
                {
                    "field": "flags.participation_reduces_usage_value",
                    "default": participation_reduces_usage_value,
                    "question": "Does participation reduce the usage value?",
                },
            ],
        }
    )


def make_key(model_code: str) -> CarKey:
    return CarKey.parse(f"2026-2026-1-01229-{model_code}")


CURRENT_KEY = make_key("00001")  # the employee's own car: conventional, usage value 2,000
EV_KEY = make_key("00002")  # electric, usage value 1,500, group C, costs 4,300
SPARE_KEY = make_key("00003")  # hybrid, usage value 1,800, group C, costs 4,500
PRICEY_KEY = make_key("00004")  # plug-in, usage value 1,700, group C, costs 4,800
HIGHER_KEY = make_key("00005")  # group D, out of reach of a group C employee
FAMILY_KEY = make_key("00006")  # Family_Track car; its catalog cost is its Car_Cost
UNLISTED_KEY = make_key("00099")  # in the dataset, but not in the catalog

CURRENT_CAR = CarAlternative(car_key=CURRENT_KEY)
EV_CAR = CarAlternative(car_key=EV_KEY)
SPARE_CAR = CarAlternative(car_key=SPARE_KEY)
PRICEY_CAR = CarAlternative(car_key=PRICEY_KEY)
HIGHER_CAR = CarAlternative(car_key=HIGHER_KEY)
FAMILY_CAR = CarAlternative(car_key=FAMILY_KEY)
UNLISTED_CAR = CarAlternative(car_key=UNLISTED_KEY)
WAIVER = WaiverAlternative()


def catalog_car(
    name: str, key: CarKey, group: Group, cost: int, *, family_track: bool = False
) -> dict[str, Any]:
    return {
        "display_name": name,
        "official_key": str(key),
        "monthly_cost": cost,
        "group": group,
        "family_track": family_track,
    }


def make_catalog(*, ev_cost: int = 4300, family_cost: int = 6000) -> CarCatalog:
    return CarCatalog.model_validate(
        {
            "cars": [
                catalog_car("Current car", CURRENT_KEY, Group.C, 4500),
                catalog_car("Electric car", EV_KEY, Group.C, ev_cost),
                catalog_car("Spare car", SPARE_KEY, Group.C, 4500),
                catalog_car("Pricey car", PRICEY_KEY, Group.C, 4800),
                catalog_car("Group D car", HIGHER_KEY, Group.D, 5200),
                catalog_car("Family car", FAMILY_KEY, Group.A, family_cost, family_track=True),
            ]
        }
    )


def make_record(key: CarKey, powertrain: Powertrain, usage_value: int) -> CarRecord:
    return CarRecord(
        key=key,
        manufacturer_name="MAKER",
        trade_name="MODEL",
        model_description="DESCRIPTION",
        engine_cc=1600,
        powertrain=powertrain,
        list_price=150_000,
        adjusted_price=150_000,
        usage_value=usage_value,
    )


RULES = make_rules()
CATALOG = make_catalog()
OCCUPATIONS = OccupationsTable.model_validate(
    {
        "occupations": [
            {"name": "Office administration", "car_waiver_allowed": True, "car_maintenance": 1500},
            {"name": "Field sales", "car_waiver_allowed": False, "car_maintenance": 0},
        ]
    }
)
RECORDS = {
    CURRENT_KEY: make_record(CURRENT_KEY, Powertrain.CONVENTIONAL, 2000),
    EV_KEY: make_record(EV_KEY, Powertrain.ELECTRIC, 1500),
    SPARE_KEY: make_record(SPARE_KEY, Powertrain.HYBRID, 1800),
    PRICEY_KEY: make_record(PRICEY_KEY, Powertrain.PLUG_IN, 1700),
    HIGHER_KEY: make_record(HIGHER_KEY, Powertrain.CONVENTIONAL, 3000),
    FAMILY_KEY: make_record(FAMILY_KEY, Powertrain.CONVENTIONAL, 2500),
    UNLISTED_KEY: make_record(UNLISTED_KEY, Powertrain.CONVENTIONAL, 2200),
}
REP_400 = RepParticipation(amount=D(400), effective_date=date(2026, 1, 1))

# A group C employee with a 12,000 salary, a 6% pension, a 2.5% study fund and 2.25 credit
# points, whose car has a usage value of 2,000 and a participation of 350. With this payroll the
# hand-checked net pay is 9,285 without a car and 8,295 with the current car.
PROFILE = EmployeeProfile(
    eligibility_group=Group.C,
    base_salary=D(12000),
    pay_components=[],
    pension_rate_percent=D(6),
    study_fund_rate_percent=D("2.5"),
    credit_points=D("2.25"),
    occupation="Office administration",
    children_under_23=4,
    has_current_car=True,
    current_car_key=CURRENT_KEY,
    current_car_participation=D(350),
)
# With no car the employee has given it up, so Today pays the maintenance of the occupation: 1,500
# for "Office administration", which allows the waiver, and nothing for "Field sales".
NO_CAR_PROFILE = PROFILE.model_copy(
    update={"has_current_car": False, "current_car_key": None, "current_car_participation": None}
)
FIELD_SALES_NO_CAR = NO_CAR_PROFILE.model_copy(update={"occupation": "Field sales"})


def make_inputs(
    *,
    rules: RulesConfig = RULES,
    catalog: CarCatalog = CATALOG,
    records: Mapping[CarKey, CarRecord] = RECORDS,
    rep_participations: Mapping[CarKey, RepParticipation] | None = None,
    rep_questions: frozenset[tuple[MissingField, CarKey | None]] | None = None,
) -> ComparisonInputs:
    return ComparisonInputs(
        rules=rules,
        catalog=catalog,
        occupations=OCCUPATIONS,
        records=records,
        rep_participations={} if rep_participations is None else rep_participations,
        rep_questions=frozenset() if rep_questions is None else rep_questions,
    )


def column(
    alternative: Alternative,
    *,
    profile: EmployeeProfile = PROFILE,
    inputs: ComparisonInputs | None = None,
) -> AlternativeResult:
    return compare(profile, [alternative], inputs or make_inputs()).alternatives[0]


def missing_fields(result: AlternativeResult) -> list[MissingField]:
    return [item.field for item in result.missing]


def shown(value: Amount | None) -> int | None:
    return None if value is None else value.shown


def test_today_with_a_car_uses_its_record_and_the_current_participation() -> None:
    today = compare(PROFILE, [], make_inputs()).today

    assert today.status == "calculated"
    assert today.car_key == CURRENT_KEY
    assert today.usage_value == Amount(D(2000), 2000)
    assert today.participation == Amount(D(350), 350)
    assert today.ev_refund == Amount(D(0), 0)
    assert today.car_maintenance == Amount(D(0), 0)
    # 12,000 - 1,105 tax - 620 insurance - 610 health tax - 720 pension - 300 study fund - 350
    assert today.net_pay == Amount(D(8295), 8295)
    assert today.payroll is not None
    assert today.payroll.net_pay == D(8295)
    assert today.missing == ()


def test_today_without_a_car_in_an_occupation_without_a_waiver_has_every_car_item_at_zero() -> None:
    today = compare(FIELD_SALES_NO_CAR, [], make_inputs()).today

    assert today.status == "calculated"
    assert today.car_key is None
    for item in (today.usage_value, today.participation, today.ev_refund, today.car_maintenance):
        assert item == Amount(D(0), 0)
    # 12,000 - 705 tax - 480 insurance - 510 health tax - 720 pension - 300 study fund
    assert today.net_pay == Amount(D(9285), 9285)
    assert today.missing == ()


def test_today_without_a_car_pays_the_maintenance_of_the_occupation() -> None:
    today = compare(NO_CAR_PROFILE, [], make_inputs()).today

    assert today.status == "calculated"
    assert today.car_key is None
    assert today.car_maintenance == Amount(D(1500), 1500)
    for item in (today.usage_value, today.participation, today.ev_refund):
        assert item == Amount(D(0), 0)
    # 13,500 - 1,005 tax - 585 insurance - 585 health tax - 720 pension - 300 study fund
    assert today.net_pay == Amount(D(10305), 10305)
    assert today.missing == ()


@pytest.mark.parametrize("occupation", [None, "Gardener"], ids=["not-provided", "not-in-the-table"])
def test_today_without_a_car_is_pending_while_the_occupation_is_unknown(
    occupation: str | None,
) -> None:
    profile = NO_CAR_PROFILE.model_copy(update={"occupation": occupation})

    comparison = compare(profile, [EV_CAR], make_inputs(rep_participations={EV_KEY: REP_400}))

    today, [ev] = comparison.today, comparison.alternatives
    assert today.status == "pending"
    assert [item.field for item in today.missing] == [MissingField.OCCUPATION]
    assert today.car_maintenance is None
    assert today.net_pay is None
    assert ev.status == "pending"
    assert missing_fields(ev) == [MissingField.OCCUPATION]
    assert ev.net_pay is None


def test_a_stored_car_is_ignored_when_the_employee_says_there_is_no_car() -> None:
    stale = NO_CAR_PROFILE.model_copy(
        update={"current_car_key": CURRENT_KEY, "current_car_participation": D(350)}
    )

    assert (
        compare(stale, [], make_inputs()).today == compare(NO_CAR_PROFILE, [], make_inputs()).today
    )


@pytest.mark.parametrize(
    ("powertrain", "refund"),
    [
        pytest.param(Powertrain.ELECTRIC, 250, id="electric"),
        pytest.param(Powertrain.HYBRID, 0, id="hybrid"),
        pytest.param(Powertrain.PLUG_IN, 0, id="plug-in"),
        pytest.param(Powertrain.CONVENTIONAL, 0, id="conventional"),
    ],
)
def test_today_gets_the_ev_refund_only_for_an_electric_car(
    powertrain: Powertrain, refund: int
) -> None:
    records = {CURRENT_KEY: make_record(CURRENT_KEY, powertrain, 2000)}

    today = compare(PROFILE, [], make_inputs(records=records)).today

    assert shown(today.ev_refund) == refund
    assert shown(today.net_pay) == 8295 + refund  # the refund is added to net pay, untaxed


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param(
            {"has_current_car": None}, [MissingField.CURRENT_CAR], id="car-question-unanswered"
        ),
        pytest.param(
            {"current_car_key": None}, [MissingField.CURRENT_CAR], id="car-not-identified"
        ),
        pytest.param(
            {"current_car_key": make_key("00098")},
            [MissingField.CURRENT_CAR],
            id="car-missing-from-the-dataset",
        ),
        pytest.param(
            {"current_car_participation": None},
            [MissingField.CURRENT_CAR_PARTICIPATION],
            id="participation-not-provided",
        ),
        pytest.param(
            {"current_car_key": None, "current_car_participation": None},
            [MissingField.CURRENT_CAR, MissingField.CURRENT_CAR_PARTICIPATION],
            id="both",
        ),
    ],
)
def test_today_is_pending_while_the_current_car_or_its_participation_is_unknown(
    changes: dict[str, Any], expected: list[MissingField]
) -> None:
    today = compare(PROFILE.model_copy(update=changes), [], make_inputs()).today

    assert today.status == "pending"
    assert [item.field for item in today.missing] == expected
    assert today.net_pay is None
    assert today.payroll is None


def test_a_pending_today_still_shows_the_items_that_are_known() -> None:
    profile = PROFILE.model_copy(update={"current_car_participation": None})

    today = compare(profile, [], make_inputs()).today

    assert today.car_key == CURRENT_KEY
    assert shown(today.usage_value) == 2000
    assert shown(today.ev_refund) == 0
    assert shown(today.car_maintenance) == 0
    assert today.participation is None


def test_a_participation_of_zero_counts_as_provided() -> None:
    profile = PROFILE.model_copy(update={"current_car_participation": D(0)})
    answered_zero = RepParticipation(amount=D(0), effective_date=date(2026, 1, 1))
    inputs = make_inputs(rep_participations={EV_KEY: answered_zero})

    today = compare(profile, [], inputs).today
    result = column(EV_CAR, profile=profile, inputs=inputs)

    assert today.status == "calculated"
    assert shown(today.net_pay) == 8645  # nothing is deducted: 8,295 + 350
    assert result.status == "calculated"
    assert shown(result.participation) == 0
    assert shown(result.net_pay) == 9055  # 8,655 + 400


def test_an_electric_alternative_is_calculated_from_its_record_and_the_rep_participation() -> None:
    result = column(EV_CAR, inputs=make_inputs(rep_participations={EV_KEY: REP_400}))

    assert result.status == "calculated"
    assert result.alternative == EV_CAR
    assert result.car_display_name == "Electric car"
    assert result.eligibility.status == "eligible"
    assert result.eligibility.is_electric is True
    assert result.missing == ()
    assert (shown(result.car_cost), shown(result.car_budget)) == (4300, 4500)
    assert shown(result.participation) == 400
    assert result.participation_effective_date == date(2026, 1, 1)
    assert result.employer_gap_share is None
    assert shown(result.usage_value) == 1500
    assert shown(result.ev_refund) == 250
    assert shown(result.car_maintenance) == 0
    assert shown(result.unused_budget) == 200
    # 12,000 - 1,005 tax - 585 insurance - 585 health tax - 720 pension - 300 study fund - 400 + 250
    assert result.net_pay == Amount(D(8655), 8655)
    # The lower usage value saves 160 (20% tax, 7% insurance and 5% health tax on 500), the
    # refund adds 250 and the higher participation takes 50.
    assert result.difference == Amount(D(360), 360)


@pytest.mark.parametrize(
    ("powertrain", "refund"),
    [
        pytest.param(Powertrain.ELECTRIC, 250, id="electric"),
        pytest.param(Powertrain.HYBRID, 0, id="hybrid"),
        pytest.param(Powertrain.PLUG_IN, 0, id="plug-in"),
        pytest.param(Powertrain.CONVENTIONAL, 0, id="conventional"),
    ],
)
def test_an_alternative_gets_the_ev_refund_only_for_an_electric_car(
    powertrain: Powertrain, refund: int
) -> None:
    records = {**RECORDS, EV_KEY: make_record(EV_KEY, powertrain, 1500)}
    inputs = make_inputs(records=records, rep_participations={EV_KEY: REP_400})

    result = column(EV_CAR, inputs=inputs)

    assert shown(result.ev_refund) == refund
    assert shown(result.net_pay) == 8405 + refund  # 8,655 with the refund of an electric car


def test_a_rep_participation_applies_only_to_the_car_it_was_answered_for() -> None:
    inputs = make_inputs(rep_participations={EV_KEY: REP_400})

    answered, other = compare(PROFILE, [EV_CAR, SPARE_CAR], inputs).alternatives

    assert answered.status == "calculated"
    assert other.status == "pending"
    assert other.participation is None
    assert other.net_pay is None
    assert other.missing == (MissingItem(MissingField.NEW_CAR_PARTICIPATION, SPARE_KEY, False),)


def test_switching_a_column_to_another_car_makes_it_pending_until_it_is_answered() -> None:
    answered_ev = make_inputs(rep_participations={EV_KEY: REP_400})
    answered_both = make_inputs(rep_participations={EV_KEY: REP_400, SPARE_KEY: REP_400})

    before = column(EV_CAR, inputs=answered_ev)
    after = column(SPARE_CAR, inputs=answered_ev)
    answered = column(SPARE_CAR, inputs=answered_both)

    assert [result.status for result in (before, after, answered)] == [
        "calculated",
        "pending",
        "calculated",
    ]
    assert missing_fields(after) == [MissingField.NEW_CAR_PARTICIPATION]


def test_the_current_car_as_an_alternative_uses_the_employees_own_participation() -> None:
    inputs = make_inputs(rep_participations={CURRENT_KEY: REP_400})

    result = column(CURRENT_CAR, inputs=inputs)

    assert result.status == "calculated"
    assert shown(result.participation) == 350  # not the 400 of a rep answer
    assert result.participation_effective_date is None
    assert result.net_pay == compare(PROFILE, [], inputs).today.net_pay
    assert result.difference == Amount(D(0), 0)


def test_the_current_car_as_an_alternative_needs_the_current_participation_not_a_new_one() -> None:
    profile = PROFILE.model_copy(update={"current_car_participation": None})

    result = column(CURRENT_CAR, profile=profile)

    assert result.status == "pending"
    assert missing_fields(result) == [MissingField.CURRENT_CAR_PARTICIPATION]


def test_the_effective_date_of_a_rep_participation_is_reported_and_changes_no_amount() -> None:
    january = RepParticipation(amount=D(400), effective_date=date(2026, 1, 1))
    december = RepParticipation(amount=D(400), effective_date=date(2026, 12, 31))

    first = column(EV_CAR, inputs=make_inputs(rep_participations={EV_KEY: january}))
    second = column(EV_CAR, inputs=make_inputs(rep_participations={EV_KEY: december}))

    assert first.participation_effective_date == date(2026, 1, 1)
    assert second.participation_effective_date == date(2026, 12, 31)
    assert first.participation == second.participation
    assert first.net_pay == second.net_pay
    assert first.difference == second.difference


def test_a_pending_today_makes_every_alternative_that_is_not_not_eligible_pending() -> None:
    profile = PROFILE.model_copy(
        update={"current_car_participation": None, "base_salary": None, "credit_points": None}
    )
    inputs = make_inputs(rep_participations={EV_KEY: REP_400})

    comparison = compare(profile, [EV_CAR, WAIVER, FAMILY_CAR, SPARE_CAR, HIGHER_CAR], inputs)

    todays_fields = [
        MissingField.BASE_SALARY,
        MissingField.CREDIT_POINTS,
        MissingField.CURRENT_CAR_PARTICIPATION,
    ]
    assert comparison.today.status == "pending"
    assert [item.field for item in comparison.today.missing] == todays_fields
    ev, waiver, family, spare, higher = comparison.alternatives
    for pending in (ev, waiver):
        assert pending.status == "pending"
        assert missing_fields(pending) == todays_fields
    # An alternative's own gaps come on top of Today's, each field once.
    for pending in (family, spare):
        assert pending.status == "pending"
        assert missing_fields(pending) == [*todays_fields, MissingField.NEW_CAR_PARTICIPATION]
    for result in (ev, waiver, family, spare):
        assert result.net_pay is None
        assert result.difference is None
    assert higher.status == "not_eligible"
    assert higher.missing == ()


def test_a_missing_field_is_marked_sent_only_for_the_question_that_was_sent() -> None:
    profile = PROFILE.model_copy(update={"occupation": "Gardener"})
    sent = frozenset(
        {(MissingField.NEW_CAR_PARTICIPATION, SPARE_KEY), (MissingField.OCCUPATION, None)}
    )

    spare, ev, waiver = compare(
        profile, [SPARE_CAR, EV_CAR, WAIVER], make_inputs(rep_questions=sent)
    ).alternatives

    assert spare.missing == (MissingItem(MissingField.NEW_CAR_PARTICIPATION, SPARE_KEY, True),)
    assert ev.missing == (MissingItem(MissingField.NEW_CAR_PARTICIPATION, EV_KEY, False),)
    assert waiver.missing == (MissingItem(MissingField.OCCUPATION, None, True),)


def test_todays_missing_participation_is_marked_sent_when_its_question_was_sent() -> None:
    profile = PROFILE.model_copy(update={"current_car_participation": None})
    sent = frozenset({(MissingField.CURRENT_CAR_PARTICIPATION, None)})

    today = compare(profile, [], make_inputs(rep_questions=sent)).today

    assert today.missing == (MissingItem(MissingField.CURRENT_CAR_PARTICIPATION, None, True),)


@pytest.mark.parametrize(
    ("alternative", "changes"),
    [
        pytest.param(HIGHER_CAR, {}, id="car-of-a-higher-group"),
        pytest.param(FAMILY_CAR, {"children_under_23": 3}, id="too-few-children"),
        pytest.param(WAIVER, {"occupation": "Field sales"}, id="occupation-without-waiver"),
    ],
)
def test_a_not_eligible_column_carries_only_the_car_the_status_and_the_reason(
    alternative: Alternative, changes: dict[str, Any]
) -> None:
    # The salary is missing as well, which does not matter: eligibility decides first.
    profile = PROFILE.model_copy(update={**changes, "base_salary": None})
    record = RECORDS[alternative.car_key] if isinstance(alternative, CarAlternative) else None

    result = column(alternative, profile=profile)

    direct = check_eligibility(profile, alternative, RULES, CATALOG, OCCUPATIONS, record)
    assert result.status == "not_eligible"
    assert result.alternative == alternative
    assert result.eligibility == direct
    assert result.eligibility.status == "not_eligible"
    assert result.eligibility.reason
    carried = {"status", "alternative", "car_display_name", "eligibility"}
    assert [
        item.name
        for item in fields(AlternativeResult)
        if item.name not in carried and getattr(result, item.name)
    ] == []


@pytest.mark.parametrize(
    ("family_cost", "employee_percent", "group", "budget", "employee", "employer"),
    [
        pytest.param(6000, 50, Group.A, 3500, 1250, 1250, id="budget-3500-cost-6000"),
        pytest.param(6000, 50, Group.D, 5200, 400, 400, id="budget-5200-cost-6000"),
        pytest.param(4500, 50, Group.C, 4500, 0, 0, id="budget-equals-the-cost"),
        pytest.param(4000, 50, Group.C, 4500, 0, 0, id="budget-above-the-cost"),
        pytest.param(6000, 30, Group.A, 3500, 750, 1750, id="uneven-shares"),
    ],
)
def test_the_family_track_gap_is_split_by_the_configured_shares(
    family_cost: int,
    employee_percent: int,
    group: Group,
    budget: int,
    employee: int,
    employer: int,
) -> None:
    rules = make_rules(employee_share_percent=employee_percent)
    profile = PROFILE.model_copy(update={"eligibility_group": group})
    inputs = make_inputs(
        rules=rules,
        catalog=make_catalog(family_cost=family_cost),
        rep_participations={FAMILY_KEY: REP_400},
    )

    result = column(FAMILY_CAR, profile=profile, inputs=inputs)

    assert result.status == "calculated"
    assert result.employee_gap_share == Amount(D(employee), employee)
    assert result.employer_gap_share == Amount(D(employer), employer)
    assert employee + employer == max(0, family_cost - budget)
    # The participation is the representative's, and the share of the gap comes on top of it.
    assert result.participation == Amount(D(400), 400)
    assert result.participation_effective_date == date(2026, 1, 1)
    # Car_Cost is the monthly cost of the catalog entry.
    assert (shown(result.car_cost), shown(result.car_budget)) == (family_cost, budget)


def test_a_family_track_car_is_pending_without_the_group_that_sets_the_budget() -> None:
    profile = PROFILE.model_copy(update={"eligibility_group": None})
    inputs = make_inputs(rep_participations={FAMILY_KEY: REP_400})

    result = column(FAMILY_CAR, profile=profile, inputs=inputs)

    assert result.eligibility.status == "eligible"  # the children count alone decides this
    assert result.status == "pending"
    assert missing_fields(result) == [MissingField.ELIGIBILITY_GROUP]
    assert shown(result.car_cost) == 6000
    assert shown(result.participation) == 400  # what is known stays on the column
    for unknown in (
        result.car_budget,
        result.employee_gap_share,
        result.employer_gap_share,
        result.unused_budget,
        result.net_pay,
    ):
        assert unknown is None


def test_a_family_track_car_is_pending_until_the_representative_gives_its_participation() -> None:
    result = column(FAMILY_CAR)

    assert result.status == "pending"
    assert missing_fields(result) == [MissingField.NEW_CAR_PARTICIPATION]
    assert result.participation is None
    # The shares need only the cost and the budget, so they are already known.
    assert (shown(result.employee_gap_share), shown(result.employer_gap_share)) == (750, 750)
    assert result.net_pay is None


def test_a_family_track_car_lists_the_participation_and_the_share_apart_and_deducts_both() -> None:
    inputs = make_inputs(rep_participations={FAMILY_KEY: REP_400})

    result = column(FAMILY_CAR, inputs=inputs)

    assert (shown(result.participation), shown(result.employee_gap_share)) == (400, 750)
    assert result.breakdown is not None
    # Both are plain net deductions, so the step from Today's 350 to 400 + 750 costs exactly 800.
    assert result.breakdown.participation == Amount(D(-800), -800)


def test_a_regular_car_is_pending_without_the_employees_group() -> None:
    profile = PROFILE.model_copy(update={"eligibility_group": None})
    inputs = make_inputs(rep_participations={EV_KEY: REP_400})

    result = column(EV_CAR, profile=profile, inputs=inputs)

    assert result.eligibility.status == "missing"
    assert result.status == "pending"
    assert missing_fields(result) == [MissingField.ELIGIBILITY_GROUP]
    assert shown(result.car_cost) == 4300
    assert shown(result.participation) == 400  # what is known stays on the column
    assert result.car_budget is None
    assert result.unused_budget is None
    assert result.net_pay is None


@pytest.mark.parametrize(
    ("alternative", "group", "expected"),
    [
        pytest.param(EV_CAR, Group.C, 200, id="cost-below-the-budget"),
        pytest.param(EV_CAR, Group.D, 900, id="budget-of-a-higher-group"),
        pytest.param(SPARE_CAR, Group.C, None, id="cost-equals-the-budget"),
        pytest.param(PRICEY_CAR, Group.C, None, id="cost-above-the-budget"),
        pytest.param(FAMILY_CAR, Group.C, None, id="family-track-cost-above-the-budget"),
        pytest.param(WAIVER, Group.C, None, id="waiver"),
    ],
)
def test_unused_budget_appears_only_when_the_budget_exceeds_the_cost(
    alternative: Alternative, group: Group, expected: int | None
) -> None:
    profile = PROFILE.model_copy(update={"eligibility_group": group})
    answered = {key: REP_400 for key in (EV_KEY, SPARE_KEY, PRICEY_KEY, FAMILY_KEY)}

    result = column(alternative, profile=profile, inputs=make_inputs(rep_participations=answered))

    assert result.status == "calculated"
    assert shown(result.unused_budget) == expected


def test_a_family_track_car_cheaper_than_the_budget_has_unused_budget_and_no_gap() -> None:
    inputs = make_inputs(
        catalog=make_catalog(family_cost=4000), rep_participations={FAMILY_KEY: REP_400}
    )

    result = column(FAMILY_CAR, inputs=inputs)

    assert shown(result.unused_budget) == 500  # 4,500 - 4,000
    assert (shown(result.employee_gap_share), shown(result.employer_gap_share)) == (0, 0)
    assert shown(result.participation) == 400


def test_unused_budget_never_changes_the_net_pay() -> None:
    answered = {EV_KEY: REP_400}

    results = [
        column(
            EV_CAR,
            inputs=make_inputs(catalog=make_catalog(ev_cost=cost), rep_participations=answered),
        )
        for cost in (4000, 4300, 4499)
    ]

    assert [shown(result.unused_budget) for result in results] == [500, 200, 1]
    assert {result.net_pay for result in results} == {Amount(D(8655), 8655)}
    assert {result.difference for result in results} == {Amount(D(360), 360)}


def test_giving_up_the_car_pays_the_occupations_car_maintenance() -> None:
    result = column(WAIVER)

    assert result.status == "calculated"
    assert result.alternative == WAIVER
    assert result.car_display_name is None
    assert result.eligibility.status == "eligible"
    assert result.eligibility.is_electric is None
    assert (
        shown(result.usage_value),
        shown(result.participation),
        shown(result.ev_refund),
        shown(result.car_maintenance),
    ) == (0, 0, 0, 1500)
    for not_applicable in (
        result.car_cost,
        result.car_budget,
        result.unused_budget,
        result.employer_gap_share,
        result.participation_effective_date,
    ):
        assert not_applicable is None
    # Gross 13,500 less 1,005 tax, 585 insurance, 585 health tax, 720 pension and 300 study fund.
    # The maintenance is outside the contribution base, so the contributions stay on 12,000.
    assert result.net_pay == Amount(D(10305), 10305)
    assert result.difference == Amount(D(2010), 2010)  # 10,305 - 8,295


@pytest.mark.parametrize("occupation", [None, "Gardener"], ids=["not-provided", "not-in-the-table"])
def test_giving_up_the_car_is_pending_while_the_occupation_is_unknown(
    occupation: str | None,
) -> None:
    profile = PROFILE.model_copy(update={"occupation": occupation})

    result = column(WAIVER, profile=profile)

    assert result.status == "pending"
    assert result.eligibility.status == "missing"
    assert result.missing == (MissingItem(MissingField.OCCUPATION, None, False),)
    assert result.car_maintenance is None
    assert result.net_pay is None


def test_missing_payroll_inputs_are_listed_once_in_script_order() -> None:
    profile = PROFILE.model_copy(
        update={
            "credit_points": None,
            "pay_components": None,
            "study_fund_rate_percent": None,
            "base_salary": None,
            "pension_rate_percent": None,
        }
    )

    comparison = compare(profile, [EV_CAR], make_inputs(rep_participations={EV_KEY: REP_400}))

    expected = [
        MissingField.BASE_SALARY,
        MissingField.PAY_COMPONENTS,
        MissingField.PENSION_RATE,
        MissingField.STUDY_FUND_RATE,
        MissingField.CREDIT_POINTS,
    ]
    assert [item.field for item in comparison.today.missing] == expected
    assert missing_fields(comparison.alternatives[0]) == expected


def test_zero_values_and_an_empty_component_list_count_as_provided() -> None:
    profile = PROFILE.model_copy(
        update={
            "base_salary": D(0),
            "pay_components": [],
            "pension_rate_percent": D(0),
            "study_fund_rate_percent": D(0),
            "credit_points": D(0),
        }
    )

    assert compare(profile, [], make_inputs()).today.status == "calculated"


def test_a_profile_value_the_calculator_rejects_is_listed_like_a_missing_one() -> None:
    negative_bonus = PayComponent.model_construct(kind="bonus", label="Holiday", amount=D(-5))
    profile = PROFILE.model_copy(update={"base_salary": D(-1), "pay_components": [negative_bonus]})

    comparison = compare(profile, [EV_CAR], make_inputs(rep_participations={EV_KEY: REP_400}))

    expected = [MissingField.BASE_SALARY, MissingField.PAY_COMPONENTS]
    assert comparison.today.status == "pending"
    assert [item.field for item in comparison.today.missing] == expected
    assert missing_fields(comparison.alternatives[0]) == expected


def test_a_record_with_a_negative_usage_value_is_an_error_and_not_a_missing_field() -> None:
    records = {**RECORDS, CURRENT_KEY: make_record(CURRENT_KEY, Powertrain.CONVENTIONAL, -1)}

    with pytest.raises(ValueError, match="usage_value"):
        compare(PROFILE, [], make_inputs(records=records))


def test_a_car_outside_the_catalog_is_pending_for_its_group_and_names_no_participation() -> None:
    result = column(UNLISTED_CAR)

    assert result.status == "pending"
    assert result.car_display_name is None
    assert result.missing == (MissingItem(MissingField.CAR_GROUP, UNLISTED_KEY, False),)
    assert shown(result.usage_value) == 2200
    assert result.car_cost is None
    assert result.participation is None
    assert result.net_pay is None


def test_a_car_outside_the_catalog_and_the_dataset_lacks_both_group_and_powertrain() -> None:
    inputs = make_inputs(records={CURRENT_KEY: RECORDS[CURRENT_KEY]})

    result = column(UNLISTED_CAR, inputs=inputs)

    assert result.missing == (
        MissingItem(MissingField.CAR_GROUP, UNLISTED_KEY, False),
        MissingItem(MissingField.POWERTRAIN, UNLISTED_KEY, False),
    )


def test_a_catalog_car_without_a_dataset_record_is_pending_for_its_powertrain() -> None:
    records = {key: record for key, record in RECORDS.items() if key != EV_KEY}
    inputs = make_inputs(records=records, rep_participations={EV_KEY: REP_400})

    result = column(EV_CAR, inputs=inputs)

    assert result.status == "pending"
    assert result.missing == (MissingItem(MissingField.POWERTRAIN, EV_KEY, False),)
    assert result.usage_value is None
    assert result.ev_refund is None
    assert shown(result.participation) == 400
    assert result.net_pay is None


PAY_COMPONENTS = [
    PayComponent(kind="commission", label="Sales", amount=D(1000)),
    PayComponent(kind="bonus", label="Holiday", amount=D(500)),
]


def direct_net_pay(
    rules: RulesConfig, *, usage_value: int, participation: int, ev_refund: int, maintenance: int
) -> Decimal:
    # The scenario table of the design, written out by hand for the profile of the test below.
    result = calculate_net_pay(
        PayrollInput(
            base_salary=D(12000),
            commissions=(D(1000),),
            bonuses=(D(500),),
            pension_rate_percent=D(6),
            study_fund_rate_percent=D("2.5"),
            credit_points=D("2.25"),
            usage_value=D(usage_value),
            participation=D(participation),
            ev_refund=D(ev_refund),
            car_maintenance=D(maintenance),
        ),
        rules,
    )
    assert isinstance(result, PayrollOk)
    return result.breakdown.net_pay


@pytest.mark.parametrize("ev_mode", ["net", "taxable_gross"])
def test_every_column_is_the_payroll_calculator_applied_to_its_scenario(
    ev_mode: Literal["net", "taxable_gross"],
) -> None:
    rules = make_rules(ev_mode=ev_mode)
    profile = PROFILE.model_copy(update={"pay_components": PAY_COMPONENTS})
    inputs = make_inputs(rules=rules, rep_participations={EV_KEY: REP_400, FAMILY_KEY: REP_400})

    comparison = compare(profile, [EV_CAR, FAMILY_CAR, WAIVER], inputs)

    columns = [comparison.today, *comparison.alternatives]
    assert [item.status for item in columns] == ["calculated"] * 4
    # Today and the current participation of 350; the EV with the rep's 400 and the refund; the
    # Family_Track car with the rep's 400 and an employee share of 750 (a 1,500 gap in group C);
    # no car at all.
    scenarios = [
        {"usage_value": 2000, "participation": 350, "ev_refund": 0, "maintenance": 0},
        {"usage_value": 1500, "participation": 400, "ev_refund": 250, "maintenance": 0},
        {"usage_value": 2500, "participation": 1150, "ev_refund": 0, "maintenance": 0},
        {"usage_value": 0, "participation": 0, "ev_refund": 0, "maintenance": 1500},
    ]
    expected = [amount(direct_net_pay(rules, **scenario)) for scenario in scenarios]
    assert [item.net_pay for item in columns] == expected
    assert comparison.today.payroll is not None
    assert comparison.today.payroll.net_pay == expected[0].exact


def test_dropping_pending_and_not_eligible_columns_changes_no_other_result() -> None:
    inputs = make_inputs(rep_participations={EV_KEY: REP_400, FAMILY_KEY: REP_400})

    everything = compare(PROFILE, [EV_CAR, HIGHER_CAR, SPARE_CAR, WAIVER, FAMILY_CAR], inputs)
    calculated_only = compare(PROFILE, [EV_CAR, WAIVER, FAMILY_CAR], inputs)

    assert [result.status for result in everything.alternatives] == [
        "calculated",
        "not_eligible",
        "pending",
        "calculated",
        "calculated",
    ]
    assert everything.today == calculated_only.today == compare(PROFILE, [], inputs).today
    kept = [everything.alternatives[position] for position in (0, 3, 4)]
    assert kept == list(calculated_only.alternatives)


def test_compare_is_repeatable_and_leaves_its_inputs_unchanged() -> None:
    inputs = make_inputs(
        rep_participations={EV_KEY: REP_400},
        rep_questions=frozenset({(MissingField.NEW_CAR_PARTICIPATION, SPARE_KEY)}),
    )
    alternatives = [EV_CAR, HIGHER_CAR, SPARE_CAR, WAIVER, FAMILY_CAR, UNLISTED_CAR]
    snapshot = copy.deepcopy((PROFILE, alternatives, inputs))

    first = compare(PROFILE, alternatives, inputs)
    second = compare(PROFILE, alternatives, inputs)

    assert first == second
    assert (PROFILE, alternatives, inputs) == snapshot
    assert [result.alternative for result in first.alternatives] == alternatives


def test_the_shown_difference_is_the_difference_of_the_shown_net_pays() -> None:
    # Usage values of 2,001 and 1,999 move the net pay by 32% of 2 (20% tax, 7% insurance and
    # 5% health tax): 8,294.68 and 8,295.32. Both show as 8,295, so the shown difference is 0,
    # although the exact difference of 0.64 would round to 1 by itself.
    records = {
        CURRENT_KEY: make_record(CURRENT_KEY, Powertrain.CONVENTIONAL, 2001),
        SPARE_KEY: make_record(SPARE_KEY, Powertrain.CONVENTIONAL, 1999),
    }
    same_participation = RepParticipation(amount=D(350), effective_date=date(2026, 1, 1))
    inputs = make_inputs(records=records, rep_participations={SPARE_KEY: same_participation})

    comparison = compare(PROFILE, [SPARE_CAR], inputs)

    assert comparison.today.net_pay == Amount(D("8294.68"), 8295)
    assert comparison.alternatives[0].net_pay == Amount(D("8295.32"), 8295)
    assert comparison.alternatives[0].difference == Amount(D("0.64"), 0)
