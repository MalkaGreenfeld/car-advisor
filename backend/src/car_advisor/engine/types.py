import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_serializer, model_validator


class Group(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class Powertrain(StrEnum):
    ELECTRIC = "electric"
    HYBRID = "hybrid"
    PLUG_IN = "plug_in"
    CONVENTIONAL = "conventional"


Money = Annotated[Decimal, Field(ge=0)]
Percent = Annotated[Decimal, Field(ge=0, le=100)]

# Text keeps the leading zeros, and strict rejects an unquoted YAML 00001, which loads as the int 1.
_Code = Annotated[str, Field(strict=True, pattern=r"^[0-9]+$")]

_KEY_TEXT = re.compile(
    r"(?P<tax_year>[0-9]+)-(?P<registration_year>[0-9]+)-(?P<vehicle_type_code>[0-9]+)"
    r"-(?P<manufacturer_code>[0-9]+)-(?P<model_code>[0-9]+)"
)


class CarKey(BaseModel, frozen=True):
    tax_year: int
    registration_year: int
    vehicle_type_code: _Code
    manufacturer_code: _Code
    model_code: _Code

    def __str__(self) -> str:
        return (
            f"{self.tax_year}-{self.registration_year}-{self.vehicle_type_code}"
            f"-{self.manufacturer_code}-{self.model_code}"
        )

    @classmethod
    def parse(cls, text: str) -> Self:
        return cls.model_validate(text)

    # Stored conversations and the API carry the key as its canonical string.
    @model_validator(mode="before")
    @classmethod
    def _split_text(cls, data: object) -> object:
        if not isinstance(data, str):
            return data
        match = _KEY_TEXT.fullmatch(data)
        if match is None:
            raise ValueError("must look like 2026-2020-1-01229-00001")
        return match.groupdict()

    @model_serializer(when_used="json")
    def _join_text(self) -> str:
        return str(self)


class CarRecord(BaseModel, frozen=True):
    key: CarKey
    manufacturer_name: str
    trade_name: str
    model_description: str
    engine_cc: int
    powertrain: Powertrain
    list_price: int
    adjusted_price: int
    usage_value: int


class PayComponent(BaseModel):
    kind: Literal["commission", "bonus"]
    label: str
    amount: Money


class CurrentCarDescription(BaseModel):
    manufacturer: str
    model: str
    registration_year: int


class EmployeeProfile(BaseModel):
    display_name: str | None = None
    eligibility_group: Group | None = None
    base_salary: Money | None = None
    # None means "not provided yet"; [] means the employee has none, which counts as provided.
    pay_components: list[PayComponent] | None = None
    pension_rate_percent: Percent | None = None
    study_fund_rate_percent: Percent | None = None
    credit_points: Annotated[Decimal, Field(ge=0)] | None = None
    occupation: str | None = None
    children_under_23: Annotated[int, Field(ge=0)] | None = None
    has_current_car: bool | None = None
    current_car: CurrentCarDescription | None = None
    # Set only after the employee confirms the key (Req 2.9).
    current_car_key: CarKey | None = None
    current_car_participation: Money | None = None


class CarAlternative(BaseModel):
    kind: Literal["car"] = "car"
    car_key: CarKey


class WaiverAlternative(BaseModel):
    kind: Literal["waiver"] = "waiver"


Alternative = Annotated[CarAlternative | WaiverAlternative, Field(discriminator="kind")]


class MissingField(StrEnum):
    ELIGIBILITY_GROUP = "eligibility_group"
    BASE_SALARY = "base_salary"
    PAY_COMPONENTS = "pay_components"
    PENSION_RATE = "pension_rate"
    STUDY_FUND_RATE = "study_fund_rate"
    CREDIT_POINTS = "credit_points"
    OCCUPATION = "occupation"
    CHILDREN_UNDER_23 = "children_under_23"
    CURRENT_CAR = "current_car"
    CURRENT_CAR_PARTICIPATION = "current_car_participation"
    NEW_CAR_PARTICIPATION = "new_car_participation"
    CAR_GROUP = "car_group"
    POWERTRAIN = "powertrain"


class RepParticipation(BaseModel, frozen=True):
    amount: Money
    effective_date: date
