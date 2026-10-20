from dataclasses import dataclass
from typing import Literal

from car_advisor.engine import copy_he
from car_advisor.engine.config import CarCatalog, CatalogCar, OccupationsTable, RulesConfig
from car_advisor.engine.types import (
    Alternative,
    CarKey,
    CarRecord,
    EmployeeProfile,
    MissingField,
    Powertrain,
    WaiverAlternative,
)


@dataclass(frozen=True)
class EligibilityResult:
    status: Literal["eligible", "not_eligible", "missing"]
    reason: str
    missing_fields: tuple[MissingField, ...]
    # None for a waiver, or when the dataset record is absent.
    is_electric: bool | None


def check_eligibility(
    profile: EmployeeProfile,
    alternative: Alternative,
    rules: RulesConfig,
    catalog: CarCatalog,
    occupations: OccupationsTable,
    record: CarRecord | None,
) -> EligibilityResult:
    if isinstance(alternative, WaiverAlternative):
        return _check_waiver(profile, occupations)
    return _check_car(profile, alternative.car_key, rules, catalog, record)


def _check_car(
    profile: EmployeeProfile,
    car_key: CarKey,
    rules: RulesConfig,
    catalog: CarCatalog,
    record: CarRecord | None,
) -> EligibilityResult:
    car = catalog.find(car_key)
    # Only the dataset record decides, never the car's name or an agent's claim (Req 5.18).
    is_electric = None if record is None else record.powertrain is Powertrain.ELECTRIC

    missing: list[MissingField] = []
    if car is None:
        missing.append(MissingField.CAR_GROUP)
    if record is None:
        missing.append(MissingField.POWERTRAIN)

    # The catalog entry says which track a car is on, so for a car outside the catalog the
    # profile field its decision would need is unknown and is not reported.
    if car is not None:
        outcome = _track_verdict(profile, car, rules)
        if isinstance(outcome, MissingField):
            missing.append(outcome)
        elif not missing:
            return _decided(*outcome, is_electric)

    return EligibilityResult("missing", copy_he.missing_data(missing), tuple(missing), is_electric)


def _track_verdict(
    profile: EmployeeProfile, car: CatalogCar, rules: RulesConfig
) -> tuple[bool, str] | MissingField:
    if car.family_track:
        children = profile.children_under_23
        if children is None:
            return MissingField.CHILDREN_UNDER_23
        threshold = rules.family_track.children_threshold
        allowed = children >= threshold
        return allowed, copy_he.family_track_reason(children, threshold, allowed=allowed)

    employee_group = profile.eligibility_group
    if employee_group is None:
        return MissingField.ELIGIBILITY_GROUP
    order = rules.groups.order
    allowed = order.index(car.group) <= order.index(employee_group)
    return allowed, copy_he.car_group_reason(car.group, employee_group, allowed=allowed)


def _check_waiver(profile: EmployeeProfile, occupations: OccupationsTable) -> EligibilityResult:
    occupation = (profile.occupation or "").strip()
    if not occupation:
        reason = copy_he.occupation_not_provided()
        return EligibilityResult("missing", reason, (MissingField.OCCUPATION,), None)

    entry = occupations.find(occupation)
    if entry is None:
        reason = copy_he.occupation_not_in_table(occupation)
        return EligibilityResult("missing", reason, (MissingField.OCCUPATION,), None)

    allowed = entry.car_waiver_allowed
    return _decided(allowed, copy_he.waiver_reason(occupation, allowed=allowed), None)


def _decided(allowed: bool, reason: str, is_electric: bool | None) -> EligibilityResult:
    return EligibilityResult("eligible" if allowed else "not_eligible", reason, (), is_electric)
