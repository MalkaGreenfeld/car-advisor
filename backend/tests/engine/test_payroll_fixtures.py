from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

import pytest
import yaml
from pydantic import BaseModel, ValidationError, model_validator

from car_advisor.engine.config import RulesConfig
from car_advisor.engine.money import display
from car_advisor.engine.payroll import (
    PayrollBreakdown,
    PayrollInput,
    PayrollOk,
    calculate_net_pay,
)
from car_advisor.engine.types import EmployeeProfile, Money, PayComponent

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "fixtures" / "payroll"
RULES_FILE = Path(__file__).resolve().parents[3] / "config" / "rules.yaml"
FIXTURE_FILES = sorted(
    path for path in FIXTURE_DIRECTORY.iterdir() if path.suffix in {".yaml", ".yml"}
)
EV_FIXTURE = FIXTURE_DIRECTORY / "group_c_switches_to_ev.yaml"


class Scenario(BaseModel, extra="forbid"):
    usage_value: Money
    participation: Money
    ev_refund: Money
    car_maintenance: Money


class Expected(BaseModel, extra="forbid"):
    """The breakdown items a fixture states.

    Every item is optional, so a golden file may state only the approved net pay.
    """

    gross_salary: Decimal | None = None
    taxable_usage_value: Decimal | None = None
    study_fund_taxable_excess: Decimal | None = None
    taxable_income: Decimal | None = None
    tax_before_credits: Decimal | None = None
    credit_points_credit: Decimal | None = None
    pension_credit: Decimal | None = None
    income_tax: Decimal | None = None
    national_insurance: Decimal | None = None
    health_tax: Decimal | None = None
    pension_contribution: Decimal | None = None
    study_fund_contribution: Decimal | None = None
    participation: Decimal | None = None
    ev_refund: Decimal | None = None
    car_maintenance: Decimal | None = None
    net_pay: Decimal | None = None

    @model_validator(mode="after")
    def _states_an_item(self) -> Self:
        if not self.stated():
            raise ValueError("must state at least one item, or the fixture checks nothing")
        return self

    def stated(self) -> dict[str, Decimal]:
        return self.model_dump(exclude_none=True)


class PayrollFixture(BaseModel, extra="forbid"):
    profile: EmployeeProfile
    scenario: Scenario
    expected: Expected

    def payroll_input(self) -> PayrollInput:
        profile, scenario = self.profile, self.scenario
        return PayrollInput(
            base_salary=profile.base_salary,
            commissions=_amounts(profile.pay_components, "commission"),
            bonuses=_amounts(profile.pay_components, "bonus"),
            pension_rate_percent=profile.pension_rate_percent,
            study_fund_rate_percent=profile.study_fund_rate_percent,
            credit_points=profile.credit_points,
            usage_value=scenario.usage_value,
            participation=scenario.participation,
            ev_refund=scenario.ev_refund,
            car_maintenance=scenario.car_maintenance,
        )


def _amounts(
    components: list[PayComponent] | None, kind: Literal["commission", "bonus"]
) -> tuple[Decimal, ...] | None:
    if components is None:
        return None
    return tuple(component.amount for component in components if component.kind == kind)


def load_fixture(path: Path) -> PayrollFixture:
    return PayrollFixture.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def mismatches(fixture: PayrollFixture, rules: RulesConfig) -> dict[str, tuple[int, int]]:
    """Map each stated item that differs in whole shekels to its (calculated, expected) pair."""
    result = calculate_net_pay(fixture.payroll_input(), rules)
    assert isinstance(result, PayrollOk), result
    differing: dict[str, tuple[int, int]] = {}
    for item, value in fixture.expected.stated().items():
        calculated, expected = display(getattr(result.breakdown, item)), display(value)
        if calculated != expected:
            differing[item] = (calculated, expected)
    return differing


@pytest.fixture(scope="module")
def rules() -> RulesConfig:
    return RulesConfig.model_validate(yaml.safe_load(RULES_FILE.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda path: path.stem)
def test_the_calculator_reproduces_every_item_a_fixture_states(
    path: Path, rules: RulesConfig
) -> None:
    assert mismatches(load_fixture(path), rules) == {}


def test_a_fixture_that_states_only_the_net_pay_is_checked_on_that_item(
    rules: RulesConfig,
) -> None:
    complete = load_fixture(EV_FIXTURE)
    net_pay = complete.expected.net_pay
    assert net_pay is not None

    matching = complete.model_copy(update={"expected": Expected(net_pay=net_pay)})
    different = complete.model_copy(update={"expected": Expected(net_pay=net_pay + 1)})

    assert mismatches(matching, rules) == {}
    assert mismatches(different, rules) == {"net_pay": (display(net_pay), display(net_pay) + 1)}


def test_a_fixture_can_state_every_item_of_the_breakdown() -> None:
    assert set(Expected.model_fields) == {item.name for item in fields(PayrollBreakdown)}


def test_at_least_three_fixtures_state_every_item_of_the_breakdown() -> None:
    complete = [
        path
        for path in FIXTURE_FILES
        if len(load_fixture(path).expected.stated()) == len(Expected.model_fields)
    ]

    assert len(complete) >= 3


@pytest.mark.parametrize(
    "expected",
    [
        pytest.param({}, id="no-item"),
        pytest.param({"net_pay": 1, "gross_salry": 2}, id="unknown-item"),
    ],
)
def test_an_expected_section_with_no_item_or_an_unknown_item_is_rejected(
    expected: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        Expected.model_validate(expected)
