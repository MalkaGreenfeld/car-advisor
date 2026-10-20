import traceback
from collections.abc import Collection, Mapping
from decimal import Decimal

import pytest

from car_advisor.conversations.profile import (
    ALTERNATIVE_STEP,
    FieldError,
    ProfileValidationError,
    ScriptStep,
    script_step,
    validate_profile,
)
from car_advisor.engine import copy_he
from car_advisor.engine.types import (
    CarKey,
    CurrentCarDescription,
    EmployeeProfile,
    Group,
    MissingField,
)

CAR_KEY = CarKey.parse("2026-2020-1-01229-00001")

GROUP_AND_SALARY = (MissingField.ELIGIBILITY_GROUP, MissingField.BASE_SALARY)
PAY_DETAILS = (
    MissingField.PAY_COMPONENTS,
    MissingField.PENSION_RATE,
    MissingField.STUDY_FUND_RATE,
    MissingField.CREDIT_POINTS,
)
CURRENT_CAR_DETAILS = (MissingField.CURRENT_CAR, MissingField.CURRENT_CAR_PARTICIPATION)
ALTERNATIVE = ScriptStep(ALTERNATIVE_STEP, ())

STEP_1: dict[str, object] = {"eligibility_group": Group.B, "base_salary": Decimal("16000")}
STEP_2: dict[str, object] = {
    # An empty list means the employee has none, which counts as provided.
    "pay_components": [],
    "pension_rate_percent": Decimal("6"),
    "study_fund_rate_percent": Decimal("2.5"),
    "credit_points": Decimal("2.25"),
}
STEP_3: dict[str, object] = {
    "has_current_car": True,
    "current_car_key": CAR_KEY,
    "current_car_participation": Decimal("400"),
}
IDENTIFIED_CAR: dict[str, object] = {"has_current_car": True, "current_car_key": CAR_KEY}

# A constant, so the call line that a traceback prints does not hold the value itself.
REJECTED_SALARY = "-98765"


def profile_of(*parts: Mapping[str, object]) -> EmployeeProfile:
    return EmployeeProfile.model_validate(
        {key: value for part in parts for key, value in part.items()}
    )


def test_the_steps_come_in_the_order_of_the_script() -> None:
    walked = [
        script_step(profile_of(*given), ())
        for given in ((), (STEP_1,), (STEP_1, STEP_2), (STEP_1, STEP_2, STEP_3))
    ]

    assert walked == [
        ScriptStep(1, GROUP_AND_SALARY),
        ScriptStep(2, PAY_DETAILS),
        ScriptStep(3, CURRENT_CAR_DETAILS),
        ALTERNATIVE,
    ]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param((STEP_1, STEP_2), ScriptStep(3, CURRENT_CAR_DETAILS), id="steps-1-and-2"),
        pytest.param((STEP_1, STEP_2, STEP_3), ALTERNATIVE, id="steps-1-to-3"),
        pytest.param((STEP_1, STEP_3), ScriptStep(2, PAY_DETAILS), id="step-2-left-open"),
        pytest.param((STEP_2,), ScriptStep(1, GROUP_AND_SALARY), id="later-step-first"),
        pytest.param((STEP_3,), ScriptStep(1, GROUP_AND_SALARY), id="car-first"),
    ],
)
def test_several_fields_given_at_once_satisfy_every_step_they_cover(
    given: tuple[Mapping[str, object], ...], expected: ScriptStep
) -> None:
    assert script_step(profile_of(*given), ()) == expected


@pytest.mark.parametrize(
    ("cleared", "expected"),
    [
        pytest.param("eligibility_group", ScriptStep(1, GROUP_AND_SALARY[:1]), id="group"),
        pytest.param("base_salary", ScriptStep(1, GROUP_AND_SALARY[1:]), id="salary"),
        pytest.param("pay_components", ScriptStep(2, PAY_DETAILS[:1]), id="pay-components"),
        pytest.param("pension_rate_percent", ScriptStep(2, PAY_DETAILS[1:2]), id="pension"),
        pytest.param("study_fund_rate_percent", ScriptStep(2, PAY_DETAILS[2:3]), id="study-fund"),
        pytest.param("credit_points", ScriptStep(2, PAY_DETAILS[3:]), id="credit-points"),
        pytest.param("current_car_key", ScriptStep(3, CURRENT_CAR_DETAILS[:1]), id="car"),
        pytest.param(
            "current_car_participation", ScriptStep(3, CURRENT_CAR_DETAILS[1:]), id="participation"
        ),
    ],
)
def test_a_stored_field_is_never_listed(cleared: str, expected: ScriptStep) -> None:
    stored = {**STEP_1, **STEP_2, **STEP_3, cleared: None}

    assert script_step(EmployeeProfile.model_validate(stored), ()) == expected


@pytest.mark.parametrize(
    ("car", "expected"),
    [
        pytest.param({}, ScriptStep(3, CURRENT_CAR_DETAILS), id="not-answered"),
        pytest.param(
            {"has_current_car": False},
            ScriptStep(3, (MissingField.OCCUPATION,)),
            id="no-car-asks-for-the-occupation",
        ),
        pytest.param(
            {"has_current_car": False, "occupation": "Office administration"},
            ALTERNATIVE,
            id="no-car-with-an-occupation",
        ),
        pytest.param(
            {
                "has_current_car": True,
                "current_car_key": IDENTIFIED_CAR["current_car_key"],
                "current_car_participation": Decimal("400"),
            },
            ALTERNATIVE,
            id="a-car-needs-no-occupation",
        ),
        pytest.param(
            {"current_car_participation": Decimal("400")},
            ScriptStep(3, CURRENT_CAR_DETAILS[:1]),
            id="participation-before-the-car-question",
        ),
        pytest.param(
            {
                "has_current_car": True,
                "current_car": CurrentCarDescription(
                    manufacturer="Kia", model="Niro", registration_year=2020
                ),
            },
            ScriptStep(3, CURRENT_CAR_DETAILS),
            id="described-but-not-identified",
        ),
        pytest.param(
            IDENTIFIED_CAR, ScriptStep(3, CURRENT_CAR_DETAILS[1:]), id="identified-no-participation"
        ),
    ],
)
def test_the_current_car_step_follows_whether_there_is_a_car(
    car: Mapping[str, object], expected: ScriptStep
) -> None:
    assert script_step(profile_of(STEP_1, STEP_2, car), ()) == expected


@pytest.mark.parametrize(
    ("given", "open_fields", "expected"),
    [
        pytest.param(
            (STEP_1, STEP_2, IDENTIFIED_CAR),
            {MissingField.CURRENT_CAR_PARTICIPATION},
            ALTERNATIVE,
            id="only-missing-field-is-covered",
        ),
        pytest.param(
            (STEP_1, STEP_2),
            set(CURRENT_CAR_DETAILS),
            ALTERNATIVE,
            id="every-missing-field-is-covered",
        ),
        pytest.param(
            (STEP_1, STEP_2),
            {MissingField.CURRENT_CAR},
            ScriptStep(3, CURRENT_CAR_DETAILS[1:]),
            id="covered-field-is-left-out-of-its-step",
        ),
        pytest.param(
            (STEP_1,),
            set(PAY_DETAILS),
            ScriptStep(3, CURRENT_CAR_DETAILS),
            id="earlier-step-is-skipped-the-same-way",
        ),
        pytest.param(
            (STEP_1,),
            {MissingField.CURRENT_CAR_PARTICIPATION},
            ScriptStep(2, PAY_DETAILS),
            id="question-in-a-later-step-skips-nothing-earlier",
        ),
        pytest.param(
            (STEP_1, STEP_2, IDENTIFIED_CAR),
            {MissingField.NEW_CAR_PARTICIPATION, MissingField.OCCUPATION},
            ScriptStep(3, CURRENT_CAR_DETAILS[1:]),
            id="question-about-a-field-outside-the-steps",
        ),
    ],
)
def test_a_step_is_skipped_when_its_missing_fields_have_an_open_question(
    given: tuple[Mapping[str, object], ...],
    open_fields: Collection[MissingField],
    expected: ScriptStep,
) -> None:
    assert script_step(profile_of(*given), open_fields) == expected


def test_a_valid_change_set_becomes_the_profile() -> None:
    profile = validate_profile(
        {
            "eligibility_group": "B",
            "base_salary": "16000.50",
            "pension_rate_percent": "100",
            "children_under_23": 0,
        }
    )

    assert profile == EmployeeProfile(
        eligibility_group=Group.B,
        base_salary=Decimal("16000.50"),
        pension_rate_percent=Decimal("100"),
        children_under_23=0,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("eligibility_group", "E", id="group-outside-a-to-d"),
        pytest.param("base_salary", "-1", id="negative-salary"),
        pytest.param("current_car_participation", "-0.01", id="negative-participation"),
        pytest.param("pension_rate_percent", "100.01", id="percent-above-100"),
        pytest.param("study_fund_rate_percent", "-0.5", id="negative-percent"),
        pytest.param("credit_points", "-0.25", id="negative-credit-points"),
        pytest.param("children_under_23", -1, id="negative-children"),
        pytest.param("children_under_23", 1.5, id="fractional-children"),
        pytest.param("base_salary", "abc", id="non-number-amount"),
        pytest.param("children_under_23", "two", id="non-number-count"),
        pytest.param(
            "pay_components",
            [{"kind": "bonus", "label": "Annual", "amount": "-1"}],
            id="negative-pay-component",
        ),
        pytest.param("current_car_key", "garbage", id="malformed-car-key"),
    ],
)
def test_each_kind_of_invalid_value_gets_the_message_of_its_field(
    field: str, value: object
) -> None:
    with pytest.raises(ProfileValidationError) as caught:
        validate_profile({field: value})

    assert caught.value.errors == (FieldError(field, copy_he.PROFILE_FIELD_RANGES[field]),)


def test_every_invalid_field_is_listed_once_and_a_valid_one_is_not() -> None:
    changes: dict[str, object] = {
        "pension_rate_percent": "101",
        "base_salary": "16000",
        "pay_components": [
            {"kind": "bonus", "label": "Annual", "amount": "-1"},
            {"kind": "commission", "label": "Sales", "amount": "abc"},
        ],
        "eligibility_group": "E",
        "credit_points": "-2",
    }

    with pytest.raises(ProfileValidationError) as caught:
        validate_profile(changes)

    assert [error.field for error in caught.value.errors] == [
        "eligibility_group",
        "pay_components",
        "pension_rate_percent",
        "credit_points",
    ]


def test_the_rejected_value_never_reaches_the_error_text() -> None:
    with pytest.raises(ProfileValidationError) as caught:
        validate_profile({"base_salary": REJECTED_SALARY})

    assert "base_salary" in str(caught.value)
    assert REJECTED_SALARY.strip("-") not in "".join(traceback.format_exception(caught.value))


def test_every_profile_field_has_a_message_that_states_its_limits() -> None:
    ranges = copy_he.PROFILE_FIELD_RANGES
    letters = list(copy_he.GROUP_LETTERS.values())

    assert set(ranges) == set(EmployeeProfile.model_fields)
    assert all(message.strip() for message in ranges.values())
    assert f"{', '.join(letters[:-1])} או {letters[-1]}" in ranges["eligibility_group"]
    for field in ("pension_rate_percent", "study_fund_rate_percent"):
        assert "0" in ranges[field]
        assert "100" in ranges[field]
