from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import ValidationError

from car_advisor.engine import copy_he
from car_advisor.engine.types import EmployeeProfile, MissingField

# The desired Alternative has no profile field, so this step is reached once steps 1 to 3 have
# nothing left to ask.
ALTERNATIVE_STEP: Final = 4


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


class ProfileValidationError(Exception):
    def __init__(self, errors: Sequence[FieldError]) -> None:
        # Only the field names: a rejected value can be a salary, and salaries stay out of logs.
        super().__init__(f"invalid profile fields: {', '.join(error.field for error in errors)}")
        self.errors = tuple(errors)


@dataclass(frozen=True)
class ScriptStep:
    number: int
    missing_fields: tuple[MissingField, ...]


def validate_profile(data: Mapping[str, object]) -> EmployeeProfile:
    """Validate a whole profile, such as the stored one merged with a set of changes.

    Raises ProfileValidationError naming each invalid field once, in profile order.
    """
    try:
        return EmployeeProfile.model_validate(data)
    except ValidationError as error:
        # Pydantic's message repeats the rejected input, so it is not chained.
        raise ProfileValidationError(_field_errors(error)) from None


def script_step(profile: EmployeeProfile, open_fields: Iterable[MissingField]) -> ScriptStep:
    """Return the first script step that still has a field to ask the employee about.

    A field with an open Rep_Question is not asked again, and a step that has nothing else to
    ask is skipped (Req 9.1).
    """
    covered = frozenset(open_fields)
    for number, gaps in enumerate(_gaps_by_step(profile), start=1):
        to_ask = tuple(field for field in gaps if field not in covered)
        if to_ask:
            return ScriptStep(number, to_ask)
    return ScriptStep(ALTERNATIVE_STEP, ())


def _field_errors(error: ValidationError) -> list[FieldError]:
    # A nested error is located inside its profile field, like ("pay_components", 1, "amount").
    fields = dict.fromkeys(str(detail["loc"][0]) for detail in error.errors())
    return [FieldError(field, copy_he.PROFILE_FIELD_RANGES[field]) for field in fields]


def _gaps_by_step(profile: EmployeeProfile) -> tuple[tuple[MissingField, ...], ...]:
    """The fields of steps 1 to 3 that the profile lacks, in the order of the script."""
    has_car = profile.has_current_car
    steps = (
        (
            (MissingField.ELIGIBILITY_GROUP, profile.eligibility_group is None),
            (MissingField.BASE_SALARY, profile.base_salary is None),
        ),
        (
            # An empty list is an answer: the employee has no additional components.
            (MissingField.PAY_COMPONENTS, profile.pay_components is None),
            (MissingField.PENSION_RATE, profile.pension_rate_percent is None),
            (MissingField.STUDY_FUND_RATE, profile.study_fund_rate_percent is None),
            (MissingField.CREDIT_POINTS, profile.credit_points is None),
        ),
        (
            # The key marks the car as identified, which its description alone does not.
            (
                MissingField.CURRENT_CAR,
                has_car is None or (has_car and profile.current_car_key is None),
            ),
            # Asked together with the car, so one question covers both, until the employee
            # says there is no car.
            (
                MissingField.CURRENT_CAR_PARTICIPATION,
                has_car is not False and profile.current_car_participation is None,
            ),
            # With no car, Today pays the maintenance of the occupation.
            (MissingField.OCCUPATION, has_car is False and profile.occupation is None),
        ),
    )
    return tuple(tuple(field for field, lacking in step if lacking) for step in steps)
