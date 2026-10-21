from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Literal

from car_advisor.engine.config import CarCatalog, CatalogCar, OccupationsTable, RulesConfig
from car_advisor.engine.eligibility import EligibilityResult, check_eligibility, employee_group
from car_advisor.engine.explanation import BreakdownItem, build_explanation
from car_advisor.engine.money import Amount, amount, percent_of
from car_advisor.engine.payroll import (
    PayrollBreakdown,
    PayrollInput,
    PayrollInvalid,
    PayrollMissing,
    PayrollOk,
    calculate_net_pay,
)
from car_advisor.engine.types import (
    Alternative,
    CarAlternative,
    CarKey,
    CarRecord,
    EmployeeProfile,
    MissingField,
    Powertrain,
    RepParticipation,
)

_ZERO = Decimal(0)

ParticipationSource = Literal["employee", "catalog", "representative"]

# The fields that belong to one car. Every other missing field is about the employee. Public,
# because a question sent to Transport_Rep is matched to a missing item by the same rule.
CAR_FIELDS = frozenset(
    {MissingField.NEW_CAR_PARTICIPATION, MissingField.CAR_GROUP, MissingField.POWERTRAIN}
)

# A missing list is sorted by the declaration order of MissingField, which puts the profile
# fields before the fields of a car. The output then does not depend on which check found a field.
_FIELD_ORDER = tuple(MissingField)

# The payroll inputs that come from the profile. A pay component is named by its position, such
# as "commissions[1]", so only the part before the bracket is looked up.
_PAYROLL_PROFILE_FIELDS: Mapping[str, MissingField] = {
    "base_salary": MissingField.BASE_SALARY,
    "commissions": MissingField.PAY_COMPONENTS,
    "bonuses": MissingField.PAY_COMPONENTS,
    "pension_rate_percent": MissingField.PENSION_RATE,
    "study_fund_rate_percent": MissingField.STUDY_FUND_RATE,
    "credit_points": MissingField.CREDIT_POINTS,
}


@dataclass(frozen=True)
class ComparisonInputs:
    rules: RulesConfig
    catalog: CarCatalog
    occupations: OccupationsTable
    # The dataset records of the current car and of the alternative cars that were found.
    records: Mapping[CarKey, CarRecord]
    # The participations that Transport_Rep answered in this conversation, by car.
    rep_participations: Mapping[CarKey, RepParticipation]
    # The questions sent to Transport_Rep, open or answered, as (field, car) pairs. A question
    # about a field of the profile has no car key, see MissingItem.
    rep_questions: frozenset[tuple[MissingField, CarKey | None]]


@dataclass(frozen=True)
class MissingItem:
    field: MissingField
    # The car the field is about, for the fields that belong to one car (the participation in
    # a new car, the car group and the powertrain). None for a field of the profile.
    car_key: CarKey | None
    sent_to_rep: bool


@dataclass(frozen=True, kw_only=True)
class TodayResult:
    status: Literal["calculated", "pending"]
    # None means no car, and also that the current car is not identified yet.
    car_key: CarKey | None
    # Each item is None while it is not known. Net pay and the payroll breakdown are None
    # while Today is pending.
    participation: Amount | None
    usage_value: Amount | None
    ev_refund: Amount | None
    car_maintenance: Amount | None
    net_pay: Amount | None
    missing: tuple[MissingItem, ...]
    payroll: PayrollBreakdown | None


@dataclass(frozen=True, kw_only=True)
class Breakdown:
    """The difference from Today, split by the car item that changes it (Req 8.6, 8.7).

    The items change one after another in the order of the fields, each from its Today value to
    its alternative value. A component is the change in net pay at its step, with the effect on
    every deduction, so it depends on the items before it. The exact parts of the components add
    up to the exact difference, and the shown parts to the shown difference.
    """

    usage_value: Amount
    ev_refund: Amount
    participation: Amount
    car_maintenance: Amount


@dataclass(frozen=True, kw_only=True)
class AlternativeResult:
    status: Literal["calculated", "not_eligible", "pending"]
    alternative: Alternative
    car_display_name: str | None
    eligibility: EligibilityResult
    missing: tuple[MissingItem, ...] = ()
    # A not_eligible column carries nothing below. A pending column carries the items that are
    # already known, and no net pay, difference, breakdown or explanation.
    car_cost: Amount | None = None
    car_budget: Amount | None = None
    participation: Amount | None = None
    # Who gave the participation of a car column (Req 6.25), and for a catalog amount whether
    # a question to Transport_Rep about it is open. None for a waiver or an unknown participation.
    participation_source: ParticipationSource | None = None
    participation_awaiting_rep: bool = False
    participation_effective_date: date | None = None
    employer_gap_share: Amount | None = None
    employee_gap_share: Amount | None = None
    usage_value: Amount | None = None
    ev_refund: Amount | None = None
    car_maintenance: Amount | None = None
    unused_budget: Amount | None = None
    net_pay: Amount | None = None
    difference: Amount | None = None
    breakdown: Breakdown | None = None
    # One or two lines of text, set together with the net pay, the difference and the breakdown.
    explanation: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Comparison:
    today: TodayResult
    alternatives: tuple[AlternativeResult, ...]


@dataclass(frozen=True)
class _CarItems:
    """The four car-related payroll inputs of one scenario; None marks an unknown item."""

    usage_value: Decimal | None
    participation: Decimal | None
    ev_refund: Decimal | None
    car_maintenance: Decimal | None


@dataclass(frozen=True)
class _Scenario:
    items: _CarItems
    # The fields this scenario lacks, besides the ones that the profile and the eligibility
    # check report themselves.
    missing: tuple[MissingField, ...] = ()
    car_cost: Decimal | None = None
    car_budget: Decimal | None = None
    unused_budget: Decimal | None = None
    employer_gap_share: Decimal | None = None
    employee_gap_share: Decimal | None = None
    # The participation without the employee's share of the gap. For a Family_Track car the
    # items hold both, because the payroll deducts them together.
    own_participation: Decimal | None = None
    participation_source: ParticipationSource | None = None
    participation_awaiting_rep: bool = False
    participation_effective_date: date | None = None


@dataclass(frozen=True)
class _Participation:
    amount: Decimal | None
    missing: tuple[MissingField, ...] = ()
    source: ParticipationSource | None = None
    awaiting_rep: bool = False
    effective_date: date | None = None


@dataclass(frozen=True)
class _Gap:
    """The part of a Family_Track car's cost above the budget, split between the two sides."""

    employee_share: Decimal | None = None
    employer_share: Decimal | None = None
    missing: tuple[MissingField, ...] = ()


def compare(
    profile: EmployeeProfile, alternatives: Sequence[Alternative], inputs: ComparisonInputs
) -> Comparison:
    """Calculate Today once, then every alternative on its own against that Today.

    An alternative never depends on another one, so dropping a pending or not eligible
    alternative leaves every other result unchanged. Nothing is assumed for a missing input,
    including whether the employee has a car: an unanswered question makes Today pending
    instead of a Today without a car. A profile value that the payroll calculator rejects is
    listed like a missing one, so that it is asked for again.
    """
    today_scenario = _today_scenario(profile, inputs)
    today = _today(profile, today_scenario, inputs)
    return Comparison(
        today=today,
        alternatives=tuple(
            _alternative(profile, alternative, today, today_scenario.items, inputs)
            for alternative in alternatives
        ),
    )


def _today(profile: EmployeeProfile, scenario: _Scenario, inputs: ComparisonInputs) -> TodayResult:
    items = scenario.items
    payroll, profile_gaps = _calculate(profile, items, inputs.rules)
    return TodayResult(
        status="pending" if payroll is None else "calculated",
        car_key=profile.current_car_key if profile.has_current_car else None,
        participation=_shown(items.participation),
        usage_value=_shown(items.usage_value),
        ev_refund=_shown(items.ev_refund),
        car_maintenance=_shown(items.car_maintenance),
        net_pay=None if payroll is None else amount(payroll.net_pay),
        missing=_missing_items((*scenario.missing, *profile_gaps), None, inputs.rep_questions),
        payroll=payroll,
    )


def _alternative(
    profile: EmployeeProfile,
    alternative: Alternative,
    today: TodayResult,
    today_items: _CarItems,
    inputs: ComparisonInputs,
) -> AlternativeResult:
    car_key = alternative.car_key if isinstance(alternative, CarAlternative) else None
    car = None if car_key is None else inputs.catalog.find(car_key)
    record = None if car_key is None else inputs.records.get(car_key)

    eligibility = check_eligibility(
        profile, alternative, inputs.rules, inputs.catalog, inputs.occupations, record
    )
    display_name = None if car is None else car.display_name
    if eligibility.status == "not_eligible":
        return AlternativeResult(
            status="not_eligible",
            alternative=alternative,
            car_display_name=display_name,
            eligibility=eligibility,
        )

    if car_key is None:
        scenario = _waiver_scenario(profile, inputs.occupations)
    else:
        scenario = _car_scenario(profile, car_key, car, record, eligibility.is_electric, inputs)
    payroll, profile_gaps = _calculate(profile, scenario.items, inputs.rules)

    missing = _missing_items(
        (
            *eligibility.missing_fields,
            *scenario.missing,
            *profile_gaps,
            *(item.field for item in today.missing),
        ),
        car_key,
        inputs.rep_questions,
    )
    net_pay: Amount | None = None
    difference: Amount | None = None
    breakdown: Breakdown | None = None
    explanation: tuple[str, ...] | None = None
    status: Literal["calculated", "pending"] = "pending"
    if not missing and payroll is not None and today.net_pay is not None:
        status = "calculated"
        net_pay = amount(payroll.net_pay)
        difference = _change(today.net_pay, net_pay)
        breakdown = _breakdown(
            profile,
            inputs.rules,
            today_items=today_items,
            today_net_pay=today.net_pay,
            alternative_items=scenario.items,
            alternative_net_pay=net_pay,
        )
        explanation = _explain(difference, breakdown)

    items = scenario.items
    return AlternativeResult(
        status=status,
        alternative=alternative,
        car_display_name=display_name,
        eligibility=eligibility,
        missing=missing,
        car_cost=_shown(scenario.car_cost),
        car_budget=_shown(scenario.car_budget),
        participation=_shown(scenario.own_participation),
        participation_source=scenario.participation_source,
        participation_awaiting_rep=scenario.participation_awaiting_rep,
        participation_effective_date=scenario.participation_effective_date,
        employer_gap_share=_shown(scenario.employer_gap_share),
        employee_gap_share=_shown(scenario.employee_gap_share),
        usage_value=_shown(items.usage_value),
        ev_refund=_shown(items.ev_refund),
        car_maintenance=_shown(items.car_maintenance),
        unused_budget=_shown(scenario.unused_budget),
        net_pay=net_pay,
        difference=difference,
        breakdown=breakdown,
        explanation=explanation,
    )


def _today_scenario(profile: EmployeeProfile, inputs: ComparisonInputs) -> _Scenario:
    if profile.has_current_car is None:
        # The employee has not said whether there is a car, so neither answer is assumed.
        return _Scenario(_CarItems(None, None, None, _ZERO), (MissingField.CURRENT_CAR,))
    if not profile.has_current_car:
        entry = None if profile.occupation is None else inputs.occupations.find(profile.occupation)
        if entry is not None and entry.car_mandatory:
            # The occupation comes with a car, so a Today without one cannot exist (Req 8.17).
            return _Scenario(_CarItems(None, None, None, _ZERO), (MissingField.CURRENT_CAR,))
        # An employee with no car has given it up, so Today pays the maintenance of the occupation.
        maintenance, gaps = _waiver_maintenance(profile, inputs.occupations)
        return _Scenario(_CarItems(_ZERO, _ZERO, _ZERO, maintenance), gaps)

    key = profile.current_car_key
    record = None if key is None else inputs.records.get(key)
    missing: list[MissingField] = []
    usage_value: Decimal | None = None
    ev_refund: Decimal | None = None
    if record is None:
        # Either the car is not identified yet or the dataset has no record for it. In both
        # cases its Usage_Value is unknown, and identifying the car again is the remedy.
        missing.append(MissingField.CURRENT_CAR)
    else:
        # Today's car is not an alternative, so it is classified from its record directly.
        usage_value = Decimal(record.usage_value)
        ev_refund = _ev_refund(record.powertrain is Powertrain.ELECTRIC, inputs.rules)

    participation = profile.current_car_participation
    if participation is None:
        missing.append(MissingField.CURRENT_CAR_PARTICIPATION)
    return _Scenario(_CarItems(usage_value, participation, ev_refund, _ZERO), tuple(missing))


def _waiver_maintenance(
    profile: EmployeeProfile, occupations: OccupationsTable
) -> tuple[Decimal | None, tuple[MissingField, ...]]:
    entry = None if profile.occupation is None else occupations.find(profile.occupation)
    if entry is None:
        return None, (MissingField.OCCUPATION,)
    return entry.car_maintenance, ()


def _waiver_scenario(profile: EmployeeProfile, occupations: OccupationsTable) -> _Scenario:
    maintenance, gaps = _waiver_maintenance(profile, occupations)
    return _Scenario(_CarItems(_ZERO, _ZERO, _ZERO, maintenance), gaps, own_participation=_ZERO)


def _car_scenario(
    profile: EmployeeProfile,
    car_key: CarKey,
    car: CatalogCar | None,
    record: CarRecord | None,
    is_electric: bool | None,
    inputs: ComparisonInputs,
) -> _Scenario:
    rules = inputs.rules
    missing: list[MissingField] = []
    usage_value: Decimal | None = None
    ev_refund: Decimal | None = None
    if record is None:
        missing.append(MissingField.POWERTRAIN)
    else:
        usage_value = Decimal(record.usage_value)
        ev_refund = _ev_refund(is_electric is True, rules)

    if car is None:
        # The catalog entry tells the track, so the participation cannot be named without it.
        missing.append(MissingField.CAR_GROUP)
        return _Scenario(_CarItems(usage_value, None, ev_refund, _ZERO), tuple(missing))

    group = employee_group(profile, inputs.occupations)
    budget = None if group is None else rules.groups.budgets[group]
    cost = car.monthly_cost
    participation = _new_or_current_participation(profile, car_key, car, inputs)
    if car.family_track:
        gap = _family_track_gap(budget, cost, rules)
        paid = _paid(participation.amount, gap.employee_share)
    else:
        gap = _Gap()
        paid = participation.amount
    return _Scenario(
        _CarItems(usage_value, paid, ev_refund, _ZERO),
        (*missing, *participation.missing, *gap.missing),
        car_cost=cost,
        car_budget=budget,
        # Unused_Budget is for information only: it never reaches the payroll items.
        unused_budget=budget - cost if budget is not None and budget > cost else None,
        employer_gap_share=gap.employer_share,
        employee_gap_share=gap.employee_share,
        own_participation=participation.amount,
        participation_source=participation.source,
        participation_awaiting_rep=participation.awaiting_rep,
        participation_effective_date=participation.effective_date,
    )


def _family_track_gap(budget: Decimal | None, cost: Decimal, rules: RulesConfig) -> _Gap:
    if budget is None:
        # Without the occupation the group, and so the budget, is unknown (Req 6.10).
        return _Gap(missing=(MissingField.OCCUPATION,))
    gap = max(_ZERO, cost - budget)
    employee_share = percent_of(gap, rules.family_track.employee_share_percent)
    # The employer pays what the employee does not, so the shares add up to the gap exactly.
    return _Gap(employee_share, gap - employee_share)


def _paid(participation: Decimal | None, gap_share: Decimal | None) -> Decimal | None:
    """What a Family_Track car takes from net pay: the participation plus the share of the gap."""
    if participation is None or gap_share is None:
        return None
    return participation + gap_share


def _new_or_current_participation(
    profile: EmployeeProfile, car_key: CarKey, car: CatalogCar, inputs: ComparisonInputs
) -> _Participation:
    """The participation from its sources, in order (Req 6.3-6.5, 6.25).

    The current car takes only the employee's figure. A new car takes the representative's
    answer for the same car, else the catalog's Default_Participation, else it is missing.
    """
    if profile.has_current_car is True and profile.current_car_key == car_key:
        provided = profile.current_car_participation
        if provided is None:
            return _Participation(None, (MissingField.CURRENT_CAR_PARTICIPATION,))
        return _Participation(provided, source="employee")

    answer = inputs.rep_participations.get(car_key)
    if answer is not None:
        return _Participation(
            answer.amount, source="representative", effective_date=answer.effective_date
        )
    if car.default_participation is not None:
        # A question the employee asked keeps the catalog amount until it is answered (Req 6.23).
        asked = (MissingField.NEW_CAR_PARTICIPATION, car_key) in inputs.rep_questions
        return _Participation(car.default_participation, source="catalog", awaiting_rep=asked)
    return _Participation(None, (MissingField.NEW_CAR_PARTICIPATION,))


def _ev_refund(is_electric: bool, rules: RulesConfig) -> Decimal:
    return rules.ev_refund.amount if is_electric else _ZERO


def _payroll_input(profile: EmployeeProfile, items: _CarItems) -> PayrollInput:
    components = profile.pay_components
    return PayrollInput(
        base_salary=profile.base_salary,
        commissions=None
        if components is None
        else tuple(part.amount for part in components if part.kind == "commission"),
        bonuses=None
        if components is None
        else tuple(part.amount for part in components if part.kind == "bonus"),
        pension_rate_percent=profile.pension_rate_percent,
        study_fund_rate_percent=profile.study_fund_rate_percent,
        credit_points=profile.credit_points,
        usage_value=items.usage_value,
        participation=items.participation,
        ev_refund=items.ev_refund,
        car_maintenance=items.car_maintenance,
    )


def _calculate(
    profile: EmployeeProfile, items: _CarItems, rules: RulesConfig
) -> tuple[PayrollBreakdown | None, tuple[MissingField, ...]]:
    """Return the payroll breakdown, or the profile fields that stop it from being calculated.

    An unknown car item makes the calculator report it as missing. The scenario already lists
    the field behind each unknown item, so only the profile fields are taken from the report.
    """
    match calculate_net_pay(_payroll_input(profile, items), rules):
        case PayrollOk(breakdown):
            return breakdown, ()
        case PayrollMissing(names):
            return None, _profile_gaps(names)
        case PayrollInvalid(values):
            names = tuple(name for name, _ in values)
            rejected = [name for name in names if _profile_field(name) is None]
            if rejected:
                # The car items come from validated models, so this is corrupt data.
                raise ValueError(f"invalid car amounts in a scenario: {', '.join(rejected)}")
            return None, _profile_gaps(names)


def _net_pay(profile: EmployeeProfile, items: _CarItems, rules: RulesConfig) -> Amount:
    payroll, _ = _calculate(profile, items, rules)
    # Today and the alternative are both calculated, so each of their car items is known and
    # valid, and so is any mix of the two.
    assert payroll is not None
    return amount(payroll.net_pay)


def _breakdown(
    profile: EmployeeProfile,
    rules: RulesConfig,
    *,
    today_items: _CarItems,
    today_net_pay: Amount,
    alternative_items: _CarItems,
    alternative_net_pay: Amount,
) -> Breakdown:
    """Attribute the difference from Today to the four car items, one step at a time.

    N0 is Today's net pay. Each step sets one more item to its alternative value, in the order
    of Req 8.6, and component k is N_k - N_(k-1). The fourth step reaches the alternative's own
    items, so N4 is its net pay. The components telescope, so they add up to the difference in
    both parts (Req 8.7). A shown part is less than 1 from its exact part, except when a net pay
    changes sign between two values that both end in .50. There the part can be exactly 1 away,
    because the add-up comes first (design, Open Questions).
    """
    with_usage_value = replace(today_items, usage_value=alternative_items.usage_value)
    with_ev_refund = replace(with_usage_value, ev_refund=alternative_items.ev_refund)
    with_participation = replace(with_ev_refund, participation=alternative_items.participation)
    after_usage_value = _net_pay(profile, with_usage_value, rules)
    after_ev_refund = _net_pay(profile, with_ev_refund, rules)
    after_participation = _net_pay(profile, with_participation, rules)
    return Breakdown(
        usage_value=_change(today_net_pay, after_usage_value),
        ev_refund=_change(after_usage_value, after_ev_refund),
        participation=_change(after_ev_refund, after_participation),
        car_maintenance=_change(after_participation, alternative_net_pay),
    )


def _explain(difference: Amount, breakdown: Breakdown) -> tuple[str, ...]:
    # Only the shown amounts go in, so every number of the text is a number of the table (Req 8.10).
    return build_explanation(
        difference.shown,
        [
            (BreakdownItem.USAGE_VALUE, breakdown.usage_value.shown),
            (BreakdownItem.EV_REFUND, breakdown.ev_refund.shown),
            (BreakdownItem.PARTICIPATION, breakdown.participation.shown),
            (BreakdownItem.CAR_MAINTENANCE, breakdown.car_maintenance.shown),
        ],
    )


def _change(before: Amount, after: Amount) -> Amount:
    """The change between two net pays, shown as the difference of the shown net pays.

    Rounding the exact change instead would break the add-up: two changes of 0.4 each show as 0,
    while the shown net pays, such as 100.3, 100.7 and 101.1, are a whole shekel apart.
    """
    return Amount(exact=after.exact - before.exact, shown=after.shown - before.shown)


def _profile_field(name: str) -> MissingField | None:
    return _PAYROLL_PROFILE_FIELDS.get(name.partition("[")[0])


def _profile_gaps(names: Iterable[str]) -> tuple[MissingField, ...]:
    return tuple(field for name in names if (field := _profile_field(name)) is not None)


def _missing_items(
    fields: Iterable[MissingField],
    car_key: CarKey | None,
    rep_questions: frozenset[tuple[MissingField, CarKey | None]],
) -> tuple[MissingItem, ...]:
    """List each field once, in script order, with the car it is about and whether it was sent."""
    items: list[MissingItem] = []
    for field in sorted(set(fields), key=_FIELD_ORDER.index):
        field_car_key = car_key if field in CAR_FIELDS else None
        items.append(MissingItem(field, field_car_key, (field, field_car_key) in rep_questions))
    return tuple(items)


def _shown(value: Decimal | None) -> Amount | None:
    return None if value is None else amount(value)
