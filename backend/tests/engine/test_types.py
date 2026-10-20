from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from car_advisor.engine.types import (
    Alternative,
    CarAlternative,
    CarKey,
    CurrentCarDescription,
    EmployeeProfile,
    Group,
    PayComponent,
    WaiverAlternative,
)

KEY_TEXT = "2026-2020-1-01229-00001"
KEY_FIELDS: dict[str, object] = {
    "tax_year": 2026,
    "registration_year": 2020,
    "vehicle_type_code": "1",
    "manufacturer_code": "01229",
    "model_code": "00001",
}


def test_car_key_string_round_trip_keeps_leading_zeros() -> None:
    key = CarKey.model_validate(KEY_FIELDS)

    assert str(key) == KEY_TEXT
    assert CarKey.parse(str(key)) == key


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("2026-2020-1-01229", id="four-parts"),
        pytest.param("2026-2020-1-01229-00001-2", id="six-parts"),
        pytest.param("2026-2020-1--00001", id="empty-part"),
        pytest.param("2026-2020-1-01229-0000A", id="letter"),
        pytest.param("2026/2020/1/01229/00001", id="wrong-separator"),
        pytest.param(" 2026-2020-1-01229-00001", id="leading-space"),
        pytest.param("2026-2020-1-01229-00001\n", id="trailing-newline"),
    ],
)
def test_parse_rejects_a_malformed_key(text: str) -> None:
    with pytest.raises(ValidationError):
        CarKey.parse(text)


@pytest.mark.parametrize("field", ["vehicle_type_code", "manufacturer_code", "model_code"])
@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("12a", id="letter"),
        pytest.param(" 12", id="space"),
        pytest.param("-12", id="sign"),
        pytest.param("1.2", id="dot"),
        pytest.param("\u0661\u0662", id="arabic-indic-digits"),
        pytest.param(1, id="int"),
    ],
)
def test_codes_accept_digit_strings_only(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as caught:
        CarKey.model_validate({**KEY_FIELDS, field: value})

    assert [error["loc"] for error in caught.value.errors()] == [(field,)]


def test_alternatives_store_the_key_as_its_canonical_string() -> None:
    adapter = TypeAdapter(list[Alternative])
    alternatives = [CarAlternative(car_key=CarKey.parse(KEY_TEXT)), WaiverAlternative()]

    stored = adapter.dump_json(alternatives)

    assert stored == b'[{"kind":"car","car_key":"2026-2020-1-01229-00001"},{"kind":"waiver"}]'
    assert adapter.validate_json(stored) == alternatives


def test_profile_json_round_trip_keeps_values_at_the_allowed_edges() -> None:
    profile = EmployeeProfile(
        display_name="Dana",
        eligibility_group=Group.D,
        base_salary=Decimal("16000"),
        pay_components=[PayComponent(kind="bonus", label="Annual", amount=Decimal("1200.50"))],
        pension_rate_percent=Decimal("100"),
        study_fund_rate_percent=Decimal("0"),
        credit_points=Decimal("2.25"),
        occupation="Sales",
        children_under_23=0,
        has_current_car=True,
        current_car=CurrentCarDescription(manufacturer="Kia", model="Niro", registration_year=2020),
        current_car_key=CarKey.parse(KEY_TEXT),
        current_car_participation=Decimal("0"),
    )

    assert EmployeeProfile.model_validate_json(profile.model_dump_json()) == profile
    assert EmployeeProfile.model_validate_json("{}") == EmployeeProfile()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("eligibility_group", "E", id="group-e"),
        pytest.param("base_salary", "-1", id="negative-amount"),
        pytest.param(
            "pay_components",
            [{"kind": "bonus", "label": "Annual", "amount": "-0.01"}],
            id="negative-pay-component",
        ),
        pytest.param("pension_rate_percent", "101", id="percent-101"),
        pytest.param("credit_points", "-0.25", id="negative-credit-points"),
        pytest.param("children_under_23", 1.5, id="fractional-children"),
        pytest.param("children_under_23", -1, id="negative-children"),
        pytest.param("base_salary", "abc", id="non-numeric"),
    ],
)
def test_profile_rejects_values_outside_the_allowed_range(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as caught:
        EmployeeProfile.model_validate({field: value})

    assert [error["loc"][0] for error in caught.value.errors()] == [field]
