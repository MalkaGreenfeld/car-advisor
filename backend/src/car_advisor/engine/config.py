import functools
import itertools
from collections.abc import Hashable, Iterable, Iterator, Mapping, Sequence
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from car_advisor.engine.types import CarKey, Group, Money, Percent


def _not_blank(text: str) -> str:
    if not text.strip():
        raise ValueError("must not be blank")
    return text


_Text = Annotated[str, AfterValidator(_not_blank)]


def _duplicates[T: Hashable](values: Iterable[T]) -> Iterator[tuple[int, int]]:
    first_seen: dict[T, int] = {}
    for index, value in enumerate(values):
        earlier = first_seen.setdefault(value, index)
        if earlier != index:
            yield index, earlier


class Groups(BaseModel, extra="forbid"):
    order: list[Group]
    budgets: dict[Group, Money]

    @field_validator("order")
    @classmethod
    def _each_group_once(cls, order: list[Group]) -> list[Group]:
        if sorted(order) != sorted(Group):
            raise ValueError(f"must list each of {', '.join(Group)} exactly once")
        return order

    @field_validator("budgets")
    @classmethod
    def _every_group_budgeted(cls, budgets: dict[Group, Decimal]) -> dict[Group, Decimal]:
        missing = [group for group in Group if group not in budgets]
        if missing:
            raise ValueError(f"missing a budget for {', '.join(missing)}")
        return budgets


class FamilyTrack(BaseModel, extra="forbid"):
    children_threshold: Annotated[int, Field(ge=1, strict=True)]
    employer_share_percent: Percent
    employee_share_percent: Percent

    @model_validator(mode="after")
    def _shares_add_up(self) -> Self:
        if self.employer_share_percent + self.employee_share_percent != 100:
            raise ValueError("employer_share_percent and employee_share_percent must add up to 100")
        return self


class EvRefund(BaseModel, extra="forbid"):
    amount: Money
    mode: Literal["net", "taxable_gross"]


class Flags(BaseModel, extra="forbid"):
    participation_reduces_usage_value: bool
    variable_pay_in_contribution_base: bool
    car_maintenance_outside_benefits_base: bool


class TaxBracket(BaseModel, extra="forbid"):
    above: Money
    up_to: Money | None
    rate_percent: Percent

    @model_validator(mode="after")
    def _ends_above_its_start(self) -> Self:
        if self.up_to is not None and self.up_to <= self.above:
            raise ValueError("up_to must be greater than above")
        return self


class IncomeTax(BaseModel, extra="forbid"):
    source: _Text
    brackets: Annotated[list[TaxBracket], Field(min_length=1)]

    @field_validator("brackets")
    @classmethod
    def _contiguous(cls, brackets: list[TaxBracket]) -> list[TaxBracket]:
        problems: list[str] = []
        if brackets[0].above != 0:
            problems.append("brackets[0].above must be 0")
        for index, (previous, current) in enumerate(itertools.pairwise(brackets), start=1):
            if previous.up_to is None:
                problems.append(f"brackets[{index - 1}] is not the last, so up_to must not be null")
            elif current.above != previous.up_to:
                kind = "gap" if current.above > previous.up_to else "overlap"
                problems.append(
                    f"{kind} between brackets[{index - 1}] and brackets[{index}]; "
                    "above must equal the previous up_to"
                )
        if brackets[-1].up_to is not None:
            problems.append(f"brackets[{len(brackets) - 1}] is the last, so up_to must be null")
        if problems:
            raise ValueError("; ".join(problems))
        return brackets


class CreditPoint(BaseModel, extra="forbid"):
    value: Money
    source: _Text


class InsuranceRates(BaseModel, extra="forbid"):
    reduced_rate_percent: Percent
    full_rate_percent: Percent


class SocialInsurance(BaseModel, extra="forbid"):
    source: _Text
    reduced_rate_threshold: Money
    max_insurable_income: Money
    national_insurance: InsuranceRates
    health_tax: InsuranceRates

    @model_validator(mode="after")
    def _threshold_within_ceiling(self) -> Self:
        if self.reduced_rate_threshold > self.max_insurable_income:
            raise ValueError("reduced_rate_threshold must not be greater than max_insurable_income")
        return self


class PensionCredit(BaseModel, extra="forbid"):
    source: _Text
    rate_percent: Percent
    qualifying_share_percent: Percent
    income_ceiling: Money


class StudyFund(BaseModel, extra="forbid"):
    source: _Text
    salary_ceiling: Money
    exempt_employer_rate_percent: Percent
    employer_rate_percent: Percent


class Payroll(BaseModel, extra="forbid"):
    net_pay_method: Literal["monthly"]
    income_tax: IncomeTax
    credit_point: CreditPoint
    social_insurance: SocialInsurance
    pension_credit: PensionCredit
    study_fund: StudyFund


class PendingItem(BaseModel, extra="forbid"):
    field: _Text
    # Left to right, so a number, even a quoted one, compares as a Decimal and false stays a bool.
    default: Annotated[Decimal | bool | str, Field(union_mode="left_to_right")]
    question: _Text


def _child(node: object, name: str) -> object:
    if isinstance(node, BaseModel) and name in type(node).model_fields:
        return getattr(node, name)
    if isinstance(node, Mapping) and name in node:
        return node[name]
    raise LookupError(name)


def _same_value(default: Decimal | bool | str, value: object) -> bool:
    # bool is an int subclass, so False would otherwise equal a Decimal 0.
    if isinstance(default, bool) or isinstance(value, bool):
        return default is value
    return default == value


class RulesConfig(BaseModel, extra="forbid"):
    groups: Groups
    family_track: FamilyTrack
    ev_refund: EvRefund
    unused_budget: Literal["not_paid"]
    flags: Flags
    payroll: Payroll
    pending: list[PendingItem]

    @field_validator("pending")
    @classmethod
    def _pending_in_sync(
        cls, pending: list[PendingItem], info: ValidationInfo
    ) -> list[PendingItem]:
        problems: list[str] = []
        for item in pending:
            section = item.field.partition(".")[0]
            # A section that failed validation already has its own errors and is absent here.
            if section in cls.model_fields and section not in info.data:
                continue
            try:
                value = functools.reduce(_child, item.field.split("."), info.data)
            except LookupError:
                problems.append(f"{item.field} is not a rules value")
                continue
            if isinstance(value, BaseModel | Mapping | list):
                problems.append(f"{item.field} is a section, not a single value")
            elif not _same_value(item.default, value):
                problems.append(
                    f"default for {item.field} is {item.default}, but the value is {value}"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return pending


class CatalogCar(BaseModel, extra="forbid"):
    display_name: _Text
    official_key: CarKey
    monthly_cost: Annotated[Decimal, Field(gt=0)]
    # Only a suggestion for the representative's answer; the engine never uses it.
    default_participation: Money | None = None
    group: Group
    family_track: bool = False


class CarCatalog(BaseModel, extra="forbid"):
    # Optional, so Strauss's real file validates without the marker.
    mock_data: str | None = None
    cars: Annotated[list[CatalogCar], Field(min_length=1)]

    @field_validator("cars")
    @classmethod
    def _unique_keys(cls, cars: list[CatalogCar]) -> list[CatalogCar]:
        problems = [
            f"cars[{index}] ({cars[index].display_name}) has the same official_key"
            f" as cars[{earlier}] ({cars[earlier].display_name})"
            for index, earlier in _duplicates(car.official_key for car in cars)
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return cars

    def find(self, key: CarKey) -> CatalogCar | None:
        return next((car for car in self.cars if car.official_key == key), None)

    def cars_for(self, group: Group, order: Sequence[Group]) -> list[CatalogCar]:
        allowed = set(order[: order.index(group) + 1])
        return [car for car in self.cars if car.group in allowed]


def _name_forms(name: str) -> set[str]:
    """The name after strip(), and both genders of a slash name: מפתח/ת is מפתח and מפתחת."""
    stripped = name.strip()
    if "/" not in stripped:
        return {stripped}
    options = []
    for word in stripped.split():
        stem, slash, ending = word.partition("/")
        options.append([stem, stem + ending] if slash else [word])
    return {stripped, *(" ".join(forms) for forms in itertools.product(*options))}


class Occupation(BaseModel, extra="forbid"):
    name: _Text
    car_waiver_allowed: bool
    car_maintenance: Money


class OccupationsTable(BaseModel, extra="forbid"):
    mock_data: str | None = None
    occupations: Annotated[list[Occupation], Field(min_length=1)]

    @field_validator("occupations")
    @classmethod
    def _unique_names(cls, occupations: list[Occupation]) -> list[Occupation]:
        problems = [
            f"occupations[{index}] ({occupations[index].name.strip()}) has the same name"
            f" as occupations[{earlier}]"
            for index, earlier in _duplicates(item.name.strip() for item in occupations)
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return occupations

    def find(self, name: str) -> Occupation | None:
        wanted = name.strip()
        return next((item for item in self.occupations if wanted in _name_forms(item.name)), None)
