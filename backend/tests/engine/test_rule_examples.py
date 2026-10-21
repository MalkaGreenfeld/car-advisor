from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from car_advisor.engine import copy_he
from car_advisor.engine.comparison import AlternativeResult, ComparisonInputs, compare
from car_advisor.engine.config import CarCatalog, OccupationsTable, RulesConfig
from car_advisor.engine.types import (
    Alternative,
    CarAlternative,
    EmployeeProfile,
    Group,
    MissingField,
    PayComponent,
    Powertrain,
    RepParticipation,
    WaiverAlternative,
)
from car_advisor.rules.loader import load_config
from tests.engine.test_comparison import catalog_car, make_key, make_record, shown

D = Decimal

CONFIG_DIRECTORY = Path(__file__).resolve().parents[3] / "config"
RULES_FILE = CONFIG_DIRECTORY / "rules.yaml"

CURRENT_KEY = make_key("00001")
GROUP_C_KEY = make_key("00002")
GROUP_D_KEY = make_key("00003")
CHEAPER_GROUP_D_KEY = make_key("00004")
FAMILY_KEY = make_key("00005")

GROUP_C_CAR = CarAlternative(car_key=GROUP_C_KEY)
GROUP_D_CAR = CarAlternative(car_key=GROUP_D_KEY)
CHEAPER_GROUP_D_CAR = CarAlternative(car_key=CHEAPER_GROUP_D_KEY)
FAMILY_CAR = CarAlternative(car_key=FAMILY_KEY)
WAIVER = WaiverAlternative()


# The cars and occupations are defined here and not read from the mock files, so that replacing
# those files with Strauss's cannot change an example. The employee's own car is outside the
# catalog, because Today needs only its dataset record.
def make_catalog(*, family_cost: int = 6000) -> CarCatalog:
    return CarCatalog.model_validate(
        {
            "cars": [
                catalog_car("Group C car", GROUP_C_KEY, Group.C, 4300),
                catalog_car("Group D car", GROUP_D_KEY, Group.D, 5000),
                catalog_car("Cheaper group D car", CHEAPER_GROUP_D_KEY, Group.D, 4500),
                catalog_car("Family car", FAMILY_KEY, Group.A, family_cost, family_track=True),
            ]
        }
    )


def occupation(
    name: str, group: Group, *, mandatory: bool = False, maintenance: int = 0
) -> dict[str, object]:
    return {
        "name": name,
        "group": group,
        "car_mandatory": mandatory,
        "car_waiver_allowed": not mandatory,
        "car_maintenance": maintenance,
    }


CATALOG = make_catalog()
OCCUPATIONS = OccupationsTable.model_validate(
    {
        "occupations": [
            occupation("Purchasing coordinator", Group.A, maintenance=1200),
            occupation("Finance manager", Group.B, maintenance=2000),
            occupation("Sales representative", Group.B, mandatory=True),
            occupation("Product manager", Group.C, maintenance=2000),
            occupation("Department manager", Group.D, maintenance=2600),
        ]
    }
)
# The employee's group comes from the occupation, so each example names one of that group.
OCCUPATION_OF = {
    Group.A: "Purchasing coordinator",
    Group.B: "Finance manager",
    Group.C: "Product manager",
    Group.D: "Department manager",
}
# The two group D cars have the same record, so only their cost sets them apart.
RECORDS = {
    CURRENT_KEY: make_record(CURRENT_KEY, Powertrain.CONVENTIONAL, 2400),
    GROUP_C_KEY: make_record(GROUP_C_KEY, Powertrain.CONVENTIONAL, 2600),
    GROUP_D_KEY: make_record(GROUP_D_KEY, Powertrain.CONVENTIONAL, 3200),
    CHEAPER_GROUP_D_KEY: make_record(CHEAPER_GROUP_D_KEY, Powertrain.CONVENTIONAL, 3200),
    FAMILY_KEY: make_record(FAMILY_KEY, Powertrain.CONVENTIONAL, 3000),
}
REP_ANSWER = RepParticipation(amount=D(350), effective_date=date(2026, 1, 1))


@pytest.fixture(scope="module")
def rules() -> RulesConfig:
    # The loader reads all three files, but the examples use the rules alone.
    return load_config(
        RULES_FILE, CONFIG_DIRECTORY / "car_catalog.yaml", CONFIG_DIRECTORY / "occupations.yaml"
    ).rules


@pytest.fixture(scope="module")
def inputs(rules: RulesConfig) -> ComparisonInputs:
    return ComparisonInputs(
        rules=rules,
        catalog=CATALOG,
        occupations=OCCUPATIONS,
        records=RECORDS,
        rep_participations={
            key: REP_ANSWER for key in (GROUP_C_KEY, GROUP_D_KEY, CHEAPER_GROUP_D_KEY, FAMILY_KEY)
        },
        rep_questions=frozenset(),
    )


def employee(
    group: Group, *, occupation: str | None = None, children_under_23: int | None = None
) -> EmployeeProfile:
    # The pay data is that of the employee in the challenge script, whatever the group.
    chosen = OCCUPATION_OF[group] if occupation is None else occupation
    assert OCCUPATIONS.find(chosen) is not None
    return EmployeeProfile(
        base_salary=D(14200),
        pay_components=[PayComponent(kind="commission", label="Commissions", amount=D(1000))],
        pension_rate_percent=D(6),
        study_fund_rate_percent=D("2.5"),
        credit_points=D("2.25"),
        occupation=chosen,
        children_under_23=children_under_23,
        has_current_car=True,
        current_car_key=CURRENT_KEY,
        current_car_participation=D(300),
    )


def column(
    alternative: Alternative, profile: EmployeeProfile, inputs: ComparisonInputs
) -> AlternativeResult:
    return compare(profile, [alternative], inputs).alternatives[0]


def family_car(group: Group, inputs: ComparisonInputs) -> AlternativeResult:
    return column(FAMILY_CAR, employee(group, children_under_23=4), inputs)


def group_c_car_with(powertrain: Powertrain, inputs: ComparisonInputs) -> AlternativeResult:
    record = inputs.records[GROUP_C_KEY].model_copy(update={"powertrain": powertrain})
    changed = replace(inputs, records={**inputs.records, GROUP_C_KEY: record})
    return column(GROUP_C_CAR, employee(Group.C), changed)


# Net pay is compared between alternatives only, so no example depends on the 2026 tax tables.
def exact_net_pay(result: AlternativeResult) -> Decimal:
    assert result.net_pay is not None
    return result.net_pay.exact


def test_family_track_budget_3500_cost_6000_splits_the_gap_1250_1250(
    inputs: ComparisonInputs,
) -> None:
    result = family_car(Group.A, inputs)

    assert (shown(result.car_budget), shown(result.car_cost)) == (3500, 6000)
    assert (shown(result.employer_gap_share), shown(result.employee_gap_share)) == (1250, 1250)


def test_family_track_budget_5200_cost_6000_splits_the_gap_400_400(
    inputs: ComparisonInputs,
) -> None:
    result = family_car(Group.D, inputs)

    assert (shown(result.car_budget), shown(result.car_cost)) == (5200, 6000)
    assert (shown(result.employer_gap_share), shown(result.employee_gap_share)) == (400, 400)


def test_family_track_budget_4500_cost_4500_has_no_gap_0_0(inputs: ComparisonInputs) -> None:
    result = family_car(Group.C, replace(inputs, catalog=make_catalog(family_cost=4500)))

    assert (shown(result.car_budget), shown(result.car_cost)) == (4500, 4500)
    assert (shown(result.employer_gap_share), shown(result.employee_gap_share)) == (0, 0)


@pytest.mark.parametrize(
    ("children", "status"),
    [
        pytest.param(4, "eligible", id="4-children"),
        pytest.param(3, "not_eligible", id="3-children"),
    ],
)
def test_family_track_is_open_from_4_children_up_to_age_23(
    children: int, status: str, inputs: ComparisonInputs
) -> None:
    result = column(FAMILY_CAR, employee(Group.A, children_under_23=children), inputs)

    assert result.eligibility.status == status


def test_group_d_employee_may_choose_a_group_c_car(inputs: ComparisonInputs) -> None:
    result = column(GROUP_C_CAR, employee(Group.D), inputs)

    assert result.eligibility.status == "eligible"
    assert result.status == "calculated"


def test_group_c_employee_may_not_choose_a_group_d_car(inputs: ComparisonInputs) -> None:
    result = column(GROUP_D_CAR, employee(Group.C), inputs)

    assert result.status == "not_eligible"
    assert result.eligibility.status == "not_eligible"
    assert result.eligibility.reason == copy_he.car_group_reason(Group.D, Group.C, allowed=False)


def test_electric_alternative_adds_250_ev_refund(inputs: ComparisonInputs) -> None:
    electric = group_c_car_with(Powertrain.ELECTRIC, inputs)
    conventional = group_c_car_with(Powertrain.CONVENTIONAL, inputs)

    assert shown(electric.ev_refund) == 250
    assert exact_net_pay(electric) - exact_net_pay(conventional) == 250


@pytest.mark.parametrize(
    "powertrain", [Powertrain.HYBRID, Powertrain.PLUG_IN, Powertrain.CONVENTIONAL]
)
def test_hybrid_plug_in_and_conventional_alternatives_add_no_ev_refund(
    powertrain: Powertrain, inputs: ComparisonInputs
) -> None:
    electric = group_c_car_with(Powertrain.ELECTRIC, inputs)
    other = group_c_car_with(powertrain, inputs)

    assert shown(other.ev_refund) == 0
    assert exact_net_pay(electric) - exact_net_pay(other) == 250


def test_unused_budget_is_not_paid_so_costs_below_the_budget_give_the_same_net_pay(
    inputs: ComparisonInputs,
) -> None:
    pricier, cheaper = compare(
        employee(Group.D), [GROUP_D_CAR, CHEAPER_GROUP_D_CAR], inputs
    ).alternatives

    assert pricier.status == cheaper.status == "calculated"
    assert (shown(pricier.unused_budget), shown(cheaper.unused_budget)) == (200, 700)
    assert pricier.net_pay == cheaper.net_pay
    assert pricier.difference == cheaper.difference


def test_giving_up_the_car_is_allowed_for_an_occupation_that_permits_it(
    inputs: ComparisonInputs,
) -> None:
    result = column(WAIVER, employee(Group.B, occupation="Finance manager"), inputs)

    assert result.eligibility.status == "eligible"
    assert result.status == "calculated"
    assert shown(result.car_maintenance) == 2000


def test_giving_up_the_car_is_refused_for_an_occupation_that_forbids_it(
    inputs: ComparisonInputs,
) -> None:
    result = column(WAIVER, employee(Group.B, occupation="Sales representative"), inputs)

    assert result.status == "not_eligible"
    assert result.eligibility.status == "not_eligible"
    assert result.eligibility.reason == copy_he.waiver_reason("Sales representative", allowed=False)
    assert result.car_maintenance is None


def test_the_group_follows_from_the_occupation(inputs: ComparisonInputs) -> None:
    group_d = column(GROUP_D_CAR, employee(Group.D), inputs)
    group_c = column(GROUP_D_CAR, employee(Group.C), inputs)

    assert (group_d.eligibility.status, shown(group_d.car_budget)) == ("eligible", 5200)
    assert group_c.eligibility.status == "not_eligible"


def test_a_new_car_without_a_rep_answer_takes_the_catalogs_default_participation(
    inputs: ComparisonInputs,
) -> None:
    document = CATALOG.model_dump(mode="json")
    document["cars"][0]["default_participation"] = 250
    unanswered = replace(inputs, catalog=CarCatalog.model_validate(document), rep_participations={})

    result = column(GROUP_C_CAR, employee(Group.C), unanswered)

    assert result.status == "calculated"
    assert (shown(result.participation), result.participation_source) == (250, "catalog")


def test_no_car_in_an_occupation_with_a_mandatory_car_leaves_today_waiting_for_the_car(
    inputs: ComparisonInputs,
) -> None:
    profile = employee(Group.B, occupation="Sales representative").model_copy(
        update={"has_current_car": False, "current_car_key": None}
    )

    today = compare(profile, [GROUP_C_CAR], inputs).today

    assert today.status == "pending"
    assert [item.field for item in today.missing] == [MissingField.CURRENT_CAR]
