from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from car_advisor.engine.config import (
    InsuranceRates,
    PensionCredit,
    RulesConfig,
    SocialInsurance,
    StudyFund,
    TaxBracket,
)
from car_advisor.engine.money import percent_of

_ZERO = Decimal(0)


@dataclass(frozen=True)
class PayrollInput:
    # None means "not provided"; 0 and an empty tuple count as provided (Req 7.3).
    base_salary: Decimal | None
    commissions: tuple[Decimal, ...] | None
    bonuses: tuple[Decimal, ...] | None
    pension_rate_percent: Decimal | None
    study_fund_rate_percent: Decimal | None
    credit_points: Decimal | None
    usage_value: Decimal | None
    participation: Decimal | None
    ev_refund: Decimal | None
    car_maintenance: Decimal | None


@dataclass(frozen=True)
class PayrollBreakdown:
    gross_salary: Decimal
    taxable_usage_value: Decimal
    study_fund_taxable_excess: Decimal
    taxable_income: Decimal
    tax_before_credits: Decimal
    # Both credits stay as calculated, before income_tax is floored at 0 (Req 7.13).
    credit_points_credit: Decimal
    pension_credit: Decimal
    income_tax: Decimal
    national_insurance: Decimal
    health_tax: Decimal
    pension_contribution: Decimal
    study_fund_contribution: Decimal
    participation: Decimal
    ev_refund: Decimal
    car_maintenance: Decimal
    net_pay: Decimal


@dataclass(frozen=True)
class PayrollOk:
    breakdown: PayrollBreakdown


@dataclass(frozen=True)
class PayrollMissing:
    fields: tuple[str, ...]


@dataclass(frozen=True)
class PayrollInvalid:
    # A commissions or bonuses component is named by its position, such as "commissions[1]".
    fields: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class _ValidInput:
    """A PayrollInput with every field present and valid; the components are summed."""

    base_salary: Decimal
    commissions: Decimal
    bonuses: Decimal
    pension_rate_percent: Decimal
    study_fund_rate_percent: Decimal
    credit_points: Decimal
    usage_value: Decimal
    participation: Decimal
    ev_refund: Decimal
    car_maintenance: Decimal


def calculate_net_pay(
    payroll_input: PayrollInput, rules: RulesConfig
) -> PayrollOk | PayrollMissing | PayrollInvalid:
    """Calculate the monthly net pay, or report why it cannot be calculated.

    Invalid values are reported before missing ones, because a negative amount is a definite
    error. Each list holds every such field, in Req 7.2 order. A value that is not a finite
    number counts as invalid. Nothing is rounded: amounts are rounded only when displayed.
    """
    checked = _validate(payroll_input)
    if not isinstance(checked, _ValidInput):
        return checked
    return PayrollOk(_breakdown(checked, rules))


def _is_amount(value: Decimal) -> bool:
    return value.is_finite() and value >= 0


def _is_percent(value: Decimal) -> bool:
    return value.is_finite() and 0 <= value <= 100


def _validate(payroll_input: PayrollInput) -> _ValidInput | PayrollMissing | PayrollInvalid:
    missing: list[str] = []
    invalid: list[tuple[str, Decimal]] = []

    # A problem is recorded and the field reads as zero. That zero is never used, because the
    # problems are returned instead of a result.
    def read(
        name: str, value: Decimal | None, is_valid: Callable[[Decimal], bool] = _is_amount
    ) -> Decimal:
        if value is None:
            missing.append(name)
            return _ZERO
        if not is_valid(value):
            invalid.append((name, value))
            return _ZERO
        return value

    def read_sum(name: str, components: tuple[Decimal, ...] | None) -> Decimal:
        if components is None:
            missing.append(name)
            return _ZERO
        parts = (read(f"{name}[{index}]", part) for index, part in enumerate(components))
        return sum(parts, start=_ZERO)

    # The arguments are read in Req 7.2 order, which is the order of both problem lists.
    values = _ValidInput(
        base_salary=read("base_salary", payroll_input.base_salary),
        commissions=read_sum("commissions", payroll_input.commissions),
        bonuses=read_sum("bonuses", payroll_input.bonuses),
        pension_rate_percent=read(
            "pension_rate_percent", payroll_input.pension_rate_percent, _is_percent
        ),
        study_fund_rate_percent=read(
            "study_fund_rate_percent", payroll_input.study_fund_rate_percent, _is_percent
        ),
        credit_points=read("credit_points", payroll_input.credit_points),
        usage_value=read("usage_value", payroll_input.usage_value),
        participation=read("participation", payroll_input.participation),
        ev_refund=read("ev_refund", payroll_input.ev_refund),
        car_maintenance=read("car_maintenance", payroll_input.car_maintenance),
    )
    if invalid:
        return PayrollInvalid(tuple(invalid))
    if missing:
        return PayrollMissing(tuple(missing))
    return values


def _breakdown(values: _ValidInput, rules: RulesConfig) -> PayrollBreakdown:
    flags = rules.flags
    payroll = rules.payroll
    ev_mode = rules.ev_refund.mode

    ev_in_gross = values.ev_refund if ev_mode == "taxable_gross" else _ZERO
    gross_salary = (
        values.base_salary
        + values.commissions
        + values.bonuses
        + values.car_maintenance
        + ev_in_gross
    )

    contribution_base = values.base_salary
    if flags.variable_pay_in_contribution_base:
        contribution_base += values.commissions + values.bonuses
    if not flags.car_maintenance_outside_benefits_base:
        contribution_base += values.car_maintenance
    pension_contribution = percent_of(contribution_base, values.pension_rate_percent)
    study_fund_contribution = percent_of(contribution_base, values.study_fund_rate_percent)

    taxable_usage_value = values.usage_value
    if flags.participation_reduces_usage_value:
        taxable_usage_value = max(_ZERO, values.usage_value - values.participation)

    study_fund_excess = _employer_study_fund_excess(
        contribution_base, values.study_fund_rate_percent, payroll.study_fund
    )
    taxable_income = gross_salary + taxable_usage_value + study_fund_excess

    tax_before_credits = _tax_before_credits(taxable_income, payroll.income_tax.brackets)
    credit_points_credit = values.credit_points * payroll.credit_point.value
    pension_credit = _pension_credit(
        pension_contribution, contribution_base, payroll.pension_credit
    )
    # The part of the credits that exceeds the tax is dropped, never applied elsewhere (Req 7.8).
    income_tax = max(_ZERO, tax_before_credits - credit_points_credit - pension_credit)

    insurance = payroll.social_insurance
    national_insurance = _insurance_tax(taxable_income, insurance, insurance.national_insurance)
    health_tax = _insurance_tax(taxable_income, insurance, insurance.health_tax)

    # Usage_Value only raises the taxable income and Participation is a net deduction (Req 6.14).
    ev_in_net = values.ev_refund if ev_mode == "net" else _ZERO
    net_pay = (
        gross_salary
        - income_tax
        - national_insurance
        - health_tax
        - pension_contribution
        - study_fund_contribution
        - values.participation
        + ev_in_net
    )

    return PayrollBreakdown(
        gross_salary=gross_salary,
        taxable_usage_value=taxable_usage_value,
        study_fund_taxable_excess=study_fund_excess,
        taxable_income=taxable_income,
        tax_before_credits=tax_before_credits,
        credit_points_credit=credit_points_credit,
        pension_credit=pension_credit,
        income_tax=income_tax,
        national_insurance=national_insurance,
        health_tax=health_tax,
        pension_contribution=pension_contribution,
        study_fund_contribution=study_fund_contribution,
        participation=values.participation,
        ev_refund=values.ev_refund,
        car_maintenance=values.car_maintenance,
        net_pay=net_pay,
    )


def _employer_study_fund_excess(
    contribution_base: Decimal, employee_rate_percent: Decimal, study_fund: StudyFund
) -> Decimal:
    # Assumed: the employer deposits only when the employee does (design, Open Questions 9).
    if employee_rate_percent <= 0:
        return _ZERO
    deposit = percent_of(contribution_base, study_fund.employer_rate_percent)
    exempt = percent_of(
        min(contribution_base, study_fund.salary_ceiling), study_fund.exempt_employer_rate_percent
    )
    return max(_ZERO, deposit - exempt)


def _tax_before_credits(taxable_income: Decimal, brackets: Sequence[TaxBracket]) -> Decimal:
    tax = _ZERO
    for bracket in brackets:
        top = taxable_income if bracket.up_to is None else min(taxable_income, bracket.up_to)
        tax += percent_of(max(_ZERO, top - bracket.above), bracket.rate_percent)
    return tax


def _pension_credit(
    pension_contribution: Decimal, contribution_base: Decimal, credit: PensionCredit
) -> Decimal:
    qualifying_cap = percent_of(
        min(contribution_base, credit.income_ceiling), credit.qualifying_share_percent
    )
    return percent_of(min(pension_contribution, qualifying_cap), credit.rate_percent)


def _insurance_tax(
    taxable_income: Decimal, insurance: SocialInsurance, rates: InsuranceRates
) -> Decimal:
    threshold = insurance.reduced_rate_threshold
    reduced_part = min(taxable_income, threshold)
    # Nothing above the maximum insurable income is charged (Req 7.9).
    full_part = max(_ZERO, min(taxable_income, insurance.max_insurable_income) - threshold)
    return percent_of(reduced_part, rates.reduced_rate_percent) + percent_of(
        full_part, rates.full_rate_percent
    )
