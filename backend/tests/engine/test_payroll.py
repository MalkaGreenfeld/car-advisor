from dataclasses import fields, replace
from decimal import Decimal
from typing import Literal

import pytest

from car_advisor.engine.config import RulesConfig
from car_advisor.engine.payroll import (
    PayrollBreakdown,
    PayrollInput,
    PayrollInvalid,
    PayrollMissing,
    PayrollOk,
    calculate_net_pay,
)

D = Decimal
SOURCE = "https://example.org/rates"


# The rates are round numbers that keep the hand arithmetic short. They are not the 2026
# parameters. Every expected value below is worked out from them by hand.
def make_rules(
    *,
    ev_mode: Literal["net", "taxable_gross"] = "net",
    participation_reduces_usage_value: bool = False,
    variable_pay_in_contribution_base: bool = False,
    car_maintenance_outside_benefits_base: bool = True,
    employer_study_fund_rate_percent: float = 7.5,
) -> RulesConfig:
    return RulesConfig.model_validate(
        {
            "groups": {
                "order": ["A", "B", "C", "D"],
                "budgets": {"A": 1000, "B": 2000, "C": 3000, "D": 4000},
            },
            "family_track": {
                "children_threshold": 4,
                "employer_share_percent": 50,
                "employee_share_percent": 50,
            },
            "ev_refund": {"amount": 250, "mode": ev_mode},
            "unused_budget": "not_paid",
            "flags": {
                "participation_reduces_usage_value": participation_reduces_usage_value,
                "variable_pay_in_contribution_base": variable_pay_in_contribution_base,
                "car_maintenance_outside_benefits_base": car_maintenance_outside_benefits_base,
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
                    "employer_rate_percent": employer_study_fund_rate_percent,
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


RULES = make_rules()

# A salary of 12,000 with a pension (6%) and a study fund (2.5%) contribution, 2.25 credit
# points and no car items.
BASELINE = PayrollInput(
    base_salary=D(12000),
    commissions=(),
    bonuses=(),
    pension_rate_percent=D(6),
    study_fund_rate_percent=D("2.5"),
    credit_points=D("2.25"),
    usage_value=D(0),
    participation=D(0),
    ev_refund=D(0),
    car_maintenance=D(0),
)

BREAKDOWN_ITEMS = [item.name for item in fields(PayrollBreakdown)]


def make_input(**changes: Decimal | tuple[Decimal, ...] | None) -> PayrollInput:
    return replace(BASELINE, **changes)


def salary_only(base_salary: str) -> PayrollInput:
    # No contributions and no credit points, so the tax and the insurance depend on the salary.
    return make_input(
        base_salary=D(base_salary),
        pension_rate_percent=D(0),
        study_fund_rate_percent=D(0),
        credit_points=D(0),
    )


def calculate(payroll_input: PayrollInput, rules: RulesConfig = RULES) -> PayrollBreakdown:
    result = calculate_net_pay(payroll_input, rules)
    assert isinstance(result, PayrollOk), result
    return result.breakdown


def breakdown(**amounts: int | str) -> PayrollBreakdown:
    # An item that is not named is 0, so each expectation lists only the amounts that matter.
    assert amounts.keys() <= set(BREAKDOWN_ITEMS), amounts.keys() - set(BREAKDOWN_ITEMS)
    return PayrollBreakdown(**{item: D(amounts.get(item, 0)) for item in BREAKDOWN_ITEMS})


BASELINE_BREAKDOWN = breakdown(
    gross_salary=12000,
    taxable_income=12000,  # the employer's study fund deposit is fully exempt below the ceiling
    tax_before_credits=1400,  # 10% of 10,000 + 20% of 2,000
    credit_points_credit=450,  # 2.25 points * 200
    pension_credit=245,  # 35% of 700; the pension is 720, capped at 7% of the 10,000 ceiling
    income_tax=705,  # 1,400 - 450 - 245
    national_insurance=480,  # 1% of 6,000 + 7% of 6,000
    health_tax=510,  # 3.5% of 6,000 + 5% of 6,000
    pension_contribution=720,  # 6% of 12,000
    study_fund_contribution=300,  # 2.5% of 12,000
    net_pay=9285,  # 12,000 - 705 - 480 - 510 - 720 - 300
)


def test_a_salary_with_contributions_and_credit_points_gets_the_hand_checked_breakdown() -> None:
    result = calculate(BASELINE)

    assert result == BASELINE_BREAKDOWN
    assert all(isinstance(getattr(result, item), Decimal) for item in BREAKDOWN_ITEMS)


def test_every_input_reaches_the_breakdown() -> None:
    payroll_input = make_input(
        commissions=(D(1000),),
        bonuses=(D(500), D(500)),
        usage_value=D(2000),
        participation=D(350),
        ev_refund=D(250),
    )

    assert calculate(payroll_input) == breakdown(
        gross_salary=14000,  # 12,000 + commission 1,000 + bonuses 1,000; no usage value
        taxable_usage_value=2000,
        taxable_income=16000,  # 14,000 + 2,000
        tax_before_credits=2200,  # 10% of 10,000 + 20% of 6,000
        credit_points_credit=450,
        pension_credit=245,
        income_tax=1505,  # 2,200 - 450 - 245
        national_insurance=760,  # 1% of 6,000 + 7% of 10,000
        health_tax=710,  # 3.5% of 6,000 + 5% of 10,000
        pension_contribution=720,  # the base is the 12,000 salary: no commissions, no bonuses
        study_fund_contribution=300,
        participation=350,
        ev_refund=250,
        net_pay=9905,  # 14,000 - 1,505 - 760 - 710 - 720 - 300 - 350 + 250
    )


@pytest.mark.parametrize(
    ("taxable_income", "tax"),
    [
        pytest.param("10000", "1000", id="first-edge"),  # 10% of 10,000
        pytest.param("10001", "1000.20", id="1-above-the-first-edge"),  # + 20% of 1
        pytest.param("20000", "3000", id="second-edge"),  # 1,000 + 20% of 10,000
        pytest.param("20001", "3000.30", id="1-above-the-second-edge"),  # + 30% of 1
        pytest.param("40000", "9000", id="third-edge"),  # 3,000 + 30% of 20,000
        pytest.param("40001", "9000.40", id="1-above-the-third-edge"),  # + 40% of 1
    ],
)
def test_a_bracket_rate_applies_only_to_the_income_inside_the_bracket(
    taxable_income: str, tax: str
) -> None:
    result = calculate(salary_only(taxable_income))

    assert result.taxable_income == D(taxable_income)
    assert result.tax_before_credits == D(tax)
    assert result.income_tax == D(tax)


@pytest.mark.parametrize(
    ("taxable_income", "national_insurance", "health_tax"),
    [
        # 1% and 3.5% of 3,000.
        pytest.param("3000", "30", "105", id="below-the-threshold"),
        # 1% and 3.5% of 6,000.
        pytest.param("6000", "60", "210", id="on-the-threshold"),
        # The reduced-rate amounts, plus 7% and 5% of the 1 above the threshold.
        pytest.param("6001", "60.07", "210.05", id="1-above-the-threshold"),
        # 60 + 7% of the 24,000 between the threshold and the maximum; 210 + 5% of the same.
        pytest.param("30000", "1740", "1410", id="on-the-maximum"),
        pytest.param("30001", "1740", "1410", id="1-above-the-maximum"),
        pytest.param("100000", "1740", "1410", id="far-above-the-maximum"),
    ],
)
def test_income_above_the_maximum_insurable_income_adds_no_insurance_or_health_tax(
    taxable_income: str, national_insurance: str, health_tax: str
) -> None:
    result = calculate(salary_only(taxable_income))

    assert result.taxable_income == D(taxable_income)
    assert result.national_insurance == D(national_insurance)
    assert result.health_tax == D(health_tax)


def test_credits_larger_than_the_tax_give_zero_income_tax_and_are_reported_as_calculated() -> None:
    payroll_input = make_input(
        base_salary=D(8000), study_fund_rate_percent=D(0), credit_points=D(4)
    )

    assert calculate(payroll_input) == breakdown(
        gross_salary=8000,
        taxable_income=8000,
        tax_before_credits=800,  # 10% of 8,000
        credit_points_credit=800,  # 4 points * 200, kept although the tax is lower
        pension_credit=168,  # 35% of the 480 pension contribution
        income_tax=0,  # the credits of 968 exceed the tax of 800
        national_insurance=200,  # 1% of 6,000 + 7% of 2,000
        health_tax=310,  # 3.5% of 6,000 + 5% of 2,000
        pension_contribution=480,  # 6% of 8,000
        net_pay=7010,  # 8,000 - 0 - 200 - 310 - 480
    )


@pytest.mark.parametrize(
    ("credit_points", "credit", "income_tax", "net_pay"),
    [
        pytest.param("2", "400", "100", "4675", id="credit-below-the-tax"),  # 500 - 400
        pytest.param("2.5", "500", "0", "4775", id="credit-equal-to-the-tax"),
        pytest.param("3", "600", "0", "4775", id="credit-above-the-tax"),
    ],
)
def test_the_excess_credit_is_dropped_and_changes_no_other_amount(
    credit_points: str, credit: str, income_tax: str, net_pay: str
) -> None:
    result = calculate(replace(salary_only("5000"), credit_points=D(credit_points)))

    assert result.tax_before_credits == D(500)  # 10% of 5,000
    assert result.credit_points_credit == D(credit)
    assert result.income_tax == D(income_tax)
    assert result.national_insurance == D(50)  # 1% of 5,000
    assert result.health_tax == D(175)  # 3.5% of 5,000
    assert result.net_pay == D(net_pay)  # 5,000 - income tax - 50 - 175


@pytest.mark.parametrize(
    ("base_salary", "pension_rate", "pension", "credit"),
    [
        # The contribution is below both limits: 35% of 480.
        pytest.param("8000", "6", "480", "168", id="contribution-qualifies-in-full"),
        # 7% of 8,000 is 560, which is below the 800 contribution: 35% of 560.
        pytest.param("8000", "10", "800", "196", id="share-of-salary-is-the-limit"),
        # 7% of the 10,000 ceiling is 700, which is below the 720 contribution: 35% of 700.
        pytest.param("12000", "6", "720", "245", id="ceiling-is-the-limit"),
        pytest.param("12000", "0", "0", "0", id="no-contribution-no-credit"),
    ],
)
def test_the_pension_credit_is_limited_to_a_share_of_the_salary_up_to_the_ceiling(
    base_salary: str, pension_rate: str, pension: str, credit: str
) -> None:
    result = calculate(replace(salary_only(base_salary), pension_rate_percent=D(pension_rate)))

    assert result.pension_contribution == D(pension)
    assert result.pension_credit == D(credit)


@pytest.mark.parametrize(
    ("ev_mode", "expected"),
    [
        pytest.param(
            "net",
            breakdown(
                gross_salary=12000,  # in net mode the refund is not taxed, so it is not gross pay
                taxable_income=12000,
                tax_before_credits=1400,
                credit_points_credit=450,
                pension_credit=245,
                income_tax=705,
                national_insurance=480,
                health_tax=510,
                pension_contribution=720,
                study_fund_contribution=300,
                ev_refund=250,
                net_pay=9535,  # 12,000 - 705 - 480 - 510 - 720 - 300 + 250
            ),
            id="net",
        ),
        pytest.param(
            "taxable_gross",
            breakdown(
                gross_salary=12250,  # 12,000 + 250
                taxable_income=12250,
                tax_before_credits=1450,  # 10% of 10,000 + 20% of 2,250
                credit_points_credit=450,
                pension_credit=245,
                income_tax=755,  # 1,450 - 450 - 245
                national_insurance="497.5",  # 60 + 7% of 6,250
                health_tax="522.5",  # 210 + 5% of 6,250
                pension_contribution=720,  # the refund is not in the contribution base
                study_fund_contribution=300,
                ev_refund=250,
                net_pay=9455,  # 12,250 - 755 - 497.5 - 522.5 - 720 - 300
            ),
            id="taxable-gross",
        ),
    ],
)
def test_ev_refund_is_added_to_net_pay_or_taxed_as_gross_pay_by_its_mode(
    ev_mode: Literal["net", "taxable_gross"], expected: PayrollBreakdown
) -> None:
    result = calculate(make_input(ev_refund=D(250)), make_rules(ev_mode=ev_mode))

    assert result == expected


# Usage value 2,000 against a participation of 350 (or 2,500), on top of the baseline salary.
@pytest.mark.parametrize(
    ("participation_reduces_usage_value", "participation", "expected"),
    [
        pytest.param(
            False,
            350,
            breakdown(
                gross_salary=12000,  # the usage value is not pay
                taxable_usage_value=2000,
                taxable_income=14000,
                tax_before_credits=1800,  # 10% of 10,000 + 20% of 4,000
                credit_points_credit=450,
                pension_credit=245,
                income_tax=1105,  # 1,800 - 450 - 245
                national_insurance=620,  # 60 + 7% of 8,000
                health_tax=610,  # 210 + 5% of 8,000
                pension_contribution=720,
                study_fund_contribution=300,
                participation=350,
                net_pay=8295,  # 12,000 - 1,105 - 620 - 610 - 720 - 300 - 350
            ),
            id="flag-off",
        ),
        pytest.param(
            True,
            350,
            breakdown(
                gross_salary=12000,
                taxable_usage_value=1650,  # 2,000 - 350
                taxable_income=13650,
                tax_before_credits=1730,  # 1,000 + 20% of 3,650
                credit_points_credit=450,
                pension_credit=245,
                income_tax=1035,  # 1,730 - 450 - 245
                national_insurance="595.5",  # 60 + 7% of 7,650
                health_tax="592.5",  # 210 + 5% of 7,650
                pension_contribution=720,
                study_fund_contribution=300,
                participation=350,  # still deducted in full from the net pay
                net_pay=8407,  # 12,000 - 1,035 - 595.5 - 592.5 - 720 - 300 - 350
            ),
            id="flag-on",
        ),
        pytest.param(
            True,
            2500,
            breakdown(
                gross_salary=12000,
                taxable_usage_value=0,  # 2,000 - 2,500 is floored at 0
                taxable_income=12000,
                tax_before_credits=1400,
                credit_points_credit=450,
                pension_credit=245,
                income_tax=705,
                national_insurance=480,
                health_tax=510,
                pension_contribution=720,
                study_fund_contribution=300,
                participation=2500,
                net_pay=6785,  # 12,000 - 705 - 480 - 510 - 720 - 300 - 2,500
            ),
            id="flag-on-participation-above-the-usage-value",
        ),
    ],
)
def test_participation_reduces_the_taxable_usage_value_only_when_the_flag_is_on(
    participation_reduces_usage_value: bool, participation: int, expected: PayrollBreakdown
) -> None:
    rules = make_rules(participation_reduces_usage_value=participation_reduces_usage_value)

    result = calculate(make_input(usage_value=D(2000), participation=D(participation)), rules)

    assert result == expected


# Commissions of 1,000 + 500 and bonuses of 2,000 + 500 on top of the baseline salary.
@pytest.mark.parametrize(
    ("variable_pay_in_contribution_base", "expected"),
    [
        pytest.param(
            False,
            breakdown(
                gross_salary=16000,  # 12,000 + 1,500 + 2,500
                taxable_income=16000,
                tax_before_credits=2200,  # 10% of 10,000 + 20% of 6,000
                credit_points_credit=450,
                pension_credit=245,
                income_tax=1505,  # 2,200 - 450 - 245
                national_insurance=760,  # 60 + 7% of 10,000
                health_tax=710,  # 210 + 5% of 10,000
                pension_contribution=720,  # 6% of the 12,000 salary
                study_fund_contribution=300,  # 2.5% of 12,000
                net_pay=12005,  # 16,000 - 1,505 - 760 - 710 - 720 - 300
            ),
            id="flag-off",
        ),
        pytest.param(
            True,
            breakdown(
                gross_salary=16000,
                taxable_income=16000,
                tax_before_credits=2200,
                credit_points_credit=450,
                pension_credit=245,  # the 960 contribution is still capped at 700
                income_tax=1505,
                national_insurance=760,
                health_tax=710,
                pension_contribution=960,  # 6% of 16,000: salary, commissions and bonuses
                study_fund_contribution=400,  # 2.5% of 16,000
                net_pay=11665,  # 16,000 - 1,505 - 760 - 710 - 960 - 400
            ),
            id="flag-on",
        ),
    ],
)
def test_commissions_and_bonuses_join_the_contribution_base_only_when_the_flag_is_on(
    variable_pay_in_contribution_base: bool, expected: PayrollBreakdown
) -> None:
    rules = make_rules(variable_pay_in_contribution_base=variable_pay_in_contribution_base)
    payroll_input = make_input(commissions=(D(1000), D(500)), bonuses=(D(2000), D(500)))

    assert calculate(payroll_input, rules) == expected


# A car maintenance of 2,000 on top of the baseline salary.
@pytest.mark.parametrize(
    ("car_maintenance_outside_benefits_base", "expected"),
    [
        pytest.param(
            True,
            breakdown(
                gross_salary=14000,  # the maintenance is gross pay either way
                taxable_income=14000,
                tax_before_credits=1800,  # 10% of 10,000 + 20% of 4,000
                credit_points_credit=450,
                pension_credit=245,
                income_tax=1105,  # 1,800 - 450 - 245
                national_insurance=620,  # 60 + 7% of 8,000
                health_tax=610,  # 210 + 5% of 8,000
                pension_contribution=720,  # 6% of the 12,000 salary
                study_fund_contribution=300,
                car_maintenance=2000,
                net_pay=10645,  # 14,000 - 1,105 - 620 - 610 - 720 - 300
            ),
            id="flag-on",
        ),
        pytest.param(
            False,
            breakdown(
                gross_salary=14000,
                taxable_income=14000,
                tax_before_credits=1800,
                credit_points_credit=450,
                pension_credit=245,
                income_tax=1105,
                national_insurance=620,
                health_tax=610,
                pension_contribution=840,  # 6% of 14,000, the maintenance included
                study_fund_contribution=350,  # 2.5% of 14,000
                car_maintenance=2000,
                net_pay=10475,  # 14,000 - 1,105 - 620 - 610 - 840 - 350
            ),
            id="flag-off",
        ),
    ],
)
def test_car_maintenance_joins_the_contribution_base_only_when_the_flag_is_off(
    car_maintenance_outside_benefits_base: bool, expected: PayrollBreakdown
) -> None:
    rules = make_rules(car_maintenance_outside_benefits_base=car_maintenance_outside_benefits_base)

    assert calculate(make_input(car_maintenance=D(2000)), rules) == expected


def test_the_employer_study_fund_deposit_above_the_ceiling_is_taxable_income() -> None:
    result = calculate(make_input(base_salary=D(20000)))

    assert result == breakdown(
        gross_salary=20000,
        # The deposit of 7.5% of 20,000 is 1,500, and 7.5% of the 16,000 ceiling, 1,200, is exempt.
        study_fund_taxable_excess=300,
        taxable_income=20300,
        tax_before_credits=3090,  # 1,000 + 20% of 10,000 + 30% of 300
        credit_points_credit=450,
        pension_credit=245,
        income_tax=2395,  # 3,090 - 450 - 245
        national_insurance=1061,  # 60 + 7% of 14,300
        health_tax=925,  # 210 + 5% of 14,300
        pension_contribution=1200,  # 6% of 20,000
        study_fund_contribution=500,  # 2.5% of 20,000
        net_pay=13919,  # 20,000 - 2,395 - 1,061 - 925 - 1,200 - 500; the excess is not paid out
    )


@pytest.mark.parametrize(
    ("base_salary", "employee_rate", "employer_rate", "excess", "taxable_income"),
    [
        # Employer deposit 7.5% of 16,000 = 1,200, all exempt.
        pytest.param("16000", "2.5", 7.5, "0", "16000", id="on-the-ceiling"),
        # 7.5% of the 1 above the ceiling.
        pytest.param("16001", "2.5", 7.5, "0.075", "16001.075", id="1-above-the-ceiling"),
        # The employer deposits only when the employee contributes.
        pytest.param("20000", "0", 7.5, "0", "20000", id="employee-does-not-contribute"),
        # The deposit of 10% of 20,000 = 2,000 less the exempt 1,200.
        pytest.param("20000", "2.5", 10.0, "800", "20800", id="employer-rate-is-the-deposit-rate"),
    ],
)
def test_the_study_fund_excess_counts_only_when_the_employee_contributes(
    base_salary: str, employee_rate: str, employer_rate: float, excess: str, taxable_income: str
) -> None:
    rules = make_rules(employer_study_fund_rate_percent=employer_rate)
    payroll_input = make_input(base_salary=D(base_salary), study_fund_rate_percent=D(employee_rate))

    result = calculate(payroll_input, rules)

    assert result.study_fund_taxable_excess == D(excess)
    assert result.taxable_income == D(taxable_income)


def test_a_missing_input_is_reported_without_a_net_pay() -> None:
    result = calculate_net_pay(make_input(credit_points=None), RULES)

    assert result == PayrollMissing(("credit_points",))


def test_every_missing_input_is_listed_in_the_order_of_requirement_7_2() -> None:
    nothing_provided = PayrollInput(**{item.name: None for item in fields(PayrollInput)})

    result = calculate_net_pay(nothing_provided, RULES)

    assert result == PayrollMissing(
        (
            "base_salary",
            "commissions",
            "bonuses",
            "pension_rate_percent",
            "study_fund_rate_percent",
            "credit_points",
            "usage_value",
            "participation",
            "ev_refund",
            "car_maintenance",
        )
    )


def test_missing_inputs_among_provided_ones_are_listed_in_order() -> None:
    payroll_input = make_input(ev_refund=None, bonuses=None, base_salary=None)

    assert calculate_net_pay(payroll_input, RULES) == PayrollMissing(
        ("base_salary", "bonuses", "ev_refund")
    )


def test_zero_and_empty_component_lists_count_as_provided() -> None:
    nothing_to_pay = PayrollInput(
        base_salary=D(0),
        commissions=(),
        bonuses=(),
        pension_rate_percent=D(0),
        study_fund_rate_percent=D(0),
        credit_points=D(0),
        usage_value=D(0),
        participation=D(0),
        ev_refund=D(0),
        car_maintenance=D(0),
    )

    assert calculate(nothing_to_pay) == breakdown()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("base_salary", "-0.01"),
        ("pension_rate_percent", "-1"),
        ("pension_rate_percent", "100.01"),
        ("study_fund_rate_percent", "101"),
        ("credit_points", "-0.5"),
        ("usage_value", "-1"),
        ("participation", "-1"),
        ("ev_refund", "-1"),
        ("car_maintenance", "-1"),
    ],
)
def test_an_invalid_input_is_reported_with_the_value_received(name: str, value: str) -> None:
    result = calculate_net_pay(make_input(**{name: D(value)}), RULES)

    assert result == PayrollInvalid(((name, D(value)),))


@pytest.mark.parametrize("name", ["commissions", "bonuses"])
def test_an_invalid_pay_component_is_named_by_its_position(name: str) -> None:
    result = calculate_net_pay(make_input(**{name: (D(100), D(-1))}), RULES)

    assert result == PayrollInvalid(((f"{name}[1]", D(-1)),))


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_a_value_that_is_not_a_finite_number_is_invalid(value: str) -> None:
    result = calculate_net_pay(
        make_input(base_salary=D(value), pension_rate_percent=D(value)), RULES
    )

    assert isinstance(result, PayrollInvalid)
    assert [name for name, _ in result.fields] == ["base_salary", "pension_rate_percent"]


@pytest.mark.parametrize(
    ("rate", "contribution"),
    [pytest.param("0", "0", id="0-percent"), pytest.param("100", "12000", id="100-percent")],
)
def test_a_contribution_percentage_of_0_or_100_is_valid(rate: str, contribution: str) -> None:
    payroll_input = make_input(pension_rate_percent=D(rate), study_fund_rate_percent=D(rate))

    result = calculate(payroll_input)

    assert result.pension_contribution == D(contribution)
    assert result.study_fund_contribution == D(contribution)


def test_invalid_inputs_win_over_missing_ones_and_all_of_them_are_listed_in_order() -> None:
    payroll_input = make_input(
        base_salary=D(-2),
        commissions=None,
        bonuses=(D(5), D(-3), D(-4)),
        pension_rate_percent=D(120),
        credit_points=None,
        car_maintenance=D(-1),
    )

    assert calculate_net_pay(payroll_input, RULES) == PayrollInvalid(
        (
            ("base_salary", D(-2)),
            ("bonuses[1]", D(-3)),
            ("bonuses[2]", D(-4)),
            ("pension_rate_percent", D(120)),
            ("car_maintenance", D(-1)),
        )
    )


def test_the_same_input_gives_the_same_result_and_leaves_the_rules_unchanged() -> None:
    rules = make_rules()
    rules_before = rules.model_dump()
    payroll_input = make_input(usage_value=D(2000), participation=D(350))

    first = calculate_net_pay(payroll_input, rules)
    second = calculate_net_pay(payroll_input, rules)

    assert first == second
    assert payroll_input == make_input(usage_value=D(2000), participation=D(350))
    assert rules.model_dump() == rules_before
