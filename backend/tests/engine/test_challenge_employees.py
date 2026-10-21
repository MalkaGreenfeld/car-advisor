"""The six sample employees of the challenge, against the reference figures of its sheet."""

from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
import yaml
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.dataset.lookup import CarLookup
from car_advisor.engine.comparison import AlternativeResult, Comparison, ComparisonInputs, compare
from car_advisor.engine.eligibility import employee_group
from car_advisor.engine.money import display
from car_advisor.engine.payroll import PayrollBreakdown, PayrollInput, PayrollOk, calculate_net_pay
from car_advisor.engine.types import (
    Alternative,
    CarAlternative,
    CarKey,
    EmployeeProfile,
    Group,
    PayComponent,
)
from car_advisor.rules.loader import AppConfig, load_config

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "challenge" / "employees.yaml"
CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


class Reference(BaseModel, extra="forbid"):
    net_pay: int
    income_tax: int
    # National Insurance plus health tax.
    insurance: int


class ChallengeEmployee(BaseModel, extra="forbid"):
    id: str
    question: str
    sheet_group: Group
    profile: EmployeeProfile
    alternative: Alternative
    today: Reference
    after: Reference
    after_taxes_include_gross_up: bool = False
    difference: int
    employee_gap_share: int | None = None
    today_car_maintenance: int | None = None


def load_employees() -> list[ChallengeEmployee]:
    document = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    return [ChallengeEmployee.model_validate(item) for item in document["employees"]]


EMPLOYEES = load_employees()
FAMILY_TRACK = [employee for employee in EMPLOYEES if employee.employee_gap_share is not None]
WITH_TODAY_MAINTENANCE = [
    employee for employee in EMPLOYEES if employee.today_car_maintenance is not None
]


def by_id(employee: ChallengeEmployee) -> str:
    return employee.id


@pytest.fixture(scope="module")
def config() -> AppConfig:
    return load_config(
        CONFIG_DIR / "rules.yaml", CONFIG_DIR / "car_catalog.yaml", CONFIG_DIR / "occupations.yaml"
    )


@pytest.fixture(scope="module")
def lookup(real_sessions: sessionmaker[Session]) -> CarLookup:
    return CarLookup(real_sessions, 2026)


def comparison_of(employee: ChallengeEmployee, config: AppConfig, lookup: CarLookup) -> Comparison:
    """Run the shipped files on the real dataset, with no question to the representative."""
    keys: list[CarKey] = []
    if employee.profile.current_car_key is not None:
        keys.append(employee.profile.current_car_key)
    if isinstance(employee.alternative, CarAlternative):
        keys.append(employee.alternative.car_key)
    inputs = ComparisonInputs(
        rules=config.rules,
        catalog=config.catalog,
        occupations=config.occupations,
        records=lookup.get_many(keys),
        rep_participations={},
        rep_questions=frozenset(),
    )
    return compare(employee.profile, [employee.alternative], inputs)


def amounts(profile: EmployeeProfile, kind: Literal["commission", "bonus"]) -> tuple[Decimal, ...]:
    components: list[PayComponent] = profile.pay_components or []
    return tuple(part.amount for part in components if part.kind == kind)


def payroll_of(
    profile: EmployeeProfile, column: AlternativeResult, config: AppConfig
) -> PayrollBreakdown:
    """The payroll of an alternative, from the items its column lists."""
    assert column.usage_value is not None
    assert column.participation is not None
    assert column.ev_refund is not None
    assert column.car_maintenance is not None
    gap_share = column.employee_gap_share
    result = calculate_net_pay(
        PayrollInput(
            base_salary=profile.base_salary,
            commissions=amounts(profile, "commission"),
            bonuses=amounts(profile, "bonus"),
            pension_rate_percent=profile.pension_rate_percent,
            study_fund_rate_percent=profile.study_fund_rate_percent,
            credit_points=profile.credit_points,
            usage_value=column.usage_value.exact,
            participation=column.participation.exact + (gap_share.exact if gap_share else 0),
            ev_refund=column.ev_refund.exact,
            car_maintenance=column.car_maintenance.exact,
        ),
        config.rules,
    )
    assert isinstance(result, PayrollOk)
    return result.breakdown


def insurance_of(breakdown: PayrollBreakdown) -> int:
    return display(breakdown.national_insurance + breakdown.health_tax)


def test_the_sheet_has_six_employees() -> None:
    assert [employee.id for employee in EMPLOYEES] == [f"E-00{number}" for number in range(1, 7)]


@pytest.mark.parametrize("employee", EMPLOYEES, ids=by_id)
def test_the_group_of_the_occupation_is_the_group_of_the_sheet(
    employee: ChallengeEmployee, config: AppConfig
) -> None:
    assert employee.profile.eligibility_group is None
    assert employee_group(employee.profile, config.occupations) == employee.sheet_group


@pytest.mark.parametrize(
    "employee", [item for item in EMPLOYEES if item.alternative.kind == "car"], ids=by_id
)
def test_a_new_car_takes_the_catalogs_participation(
    employee: ChallengeEmployee, config: AppConfig, lookup: CarLookup
) -> None:
    [column] = comparison_of(employee, config, lookup).alternatives
    assert isinstance(employee.alternative, CarAlternative)
    car = config.catalog.find(employee.alternative.car_key)

    assert car is not None
    assert column.participation is not None
    assert column.participation.exact == car.default_participation
    assert column.participation_source == "catalog"
    assert column.participation_awaiting_rep is False


@pytest.mark.parametrize("employee", EMPLOYEES, ids=by_id)
def test_net_pay_today_in_the_alternative_and_the_difference_equal_the_sheet(
    employee: ChallengeEmployee, config: AppConfig, lookup: CarLookup
) -> None:
    comparison = comparison_of(employee, config, lookup)
    [column] = comparison.alternatives

    assert comparison.today.status == "calculated"
    assert column.status == "calculated"
    assert comparison.today.net_pay is not None
    assert column.net_pay is not None
    assert column.difference is not None
    assert comparison.today.net_pay.shown == employee.today.net_pay
    assert column.net_pay.shown == employee.after.net_pay
    assert column.difference.shown == employee.difference


@pytest.mark.parametrize("employee", EMPLOYEES, ids=by_id)
def test_the_tax_and_the_insurance_of_today_equal_the_sheet(
    employee: ChallengeEmployee, config: AppConfig, lookup: CarLookup
) -> None:
    today = comparison_of(employee, config, lookup).today

    assert today.payroll is not None
    assert display(today.payroll.income_tax) == employee.today.income_tax
    assert insurance_of(today.payroll) == employee.today.insurance


@pytest.mark.parametrize("employee", EMPLOYEES, ids=by_id)
def test_the_tax_and_the_insurance_of_the_alternative_equal_the_sheet_without_a_gross_up(
    employee: ChallengeEmployee, config: AppConfig, lookup: CarLookup
) -> None:
    [column] = comparison_of(employee, config, lookup).alternatives
    assert column.ev_refund is not None
    # The sheet taxes the grossed-up refund. The engine adds exactly 250 to net pay instead.
    assert (column.ev_refund.shown == 250) is employee.after_taxes_include_gross_up
    if employee.after_taxes_include_gross_up:
        return

    breakdown = payroll_of(employee.profile, column, config)

    assert display(breakdown.income_tax) == employee.after.income_tax
    assert insurance_of(breakdown) == employee.after.insurance


@pytest.mark.parametrize("employee", FAMILY_TRACK, ids=by_id)
def test_the_family_track_gap_is_split_evenly_on_top_of_the_participation(
    employee: ChallengeEmployee, config: AppConfig, lookup: CarLookup
) -> None:
    [column] = comparison_of(employee, config, lookup).alternatives

    assert column.employee_gap_share is not None
    assert column.employer_gap_share is not None
    assert column.employee_gap_share.shown == employee.employee_gap_share
    assert column.employer_gap_share.shown == employee.employee_gap_share
    # The catalog's participation of the Carnival is deducted as well.
    assert column.participation is not None
    assert column.participation.shown == 500


@pytest.mark.parametrize("employee", WITH_TODAY_MAINTENANCE, ids=by_id)
def test_today_without_a_car_pays_the_maintenance_of_the_occupation(
    employee: ChallengeEmployee, config: AppConfig, lookup: CarLookup
) -> None:
    today = comparison_of(employee, config, lookup).today

    assert today.car_key is None
    assert today.car_maintenance is not None
    assert today.car_maintenance.shown == employee.today_car_maintenance
