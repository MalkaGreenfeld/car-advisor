import copy
from typing import Any

import pytest

from car_advisor.engine import copy_he
from car_advisor.engine.config import CarCatalog, OccupationsTable, RulesConfig
from car_advisor.engine.eligibility import EligibilityResult, check_eligibility, employee_group
from car_advisor.engine.types import (
    CarAlternative,
    CarKey,
    CarRecord,
    EmployeeProfile,
    Group,
    MissingField,
    Powertrain,
    WaiverAlternative,
)

SOURCE = "https://example.org/rates"

RULES_DOCUMENT: dict[str, Any] = {
    "groups": {
        "order": ["A", "B", "C", "D"],
        "budgets": {"A": 1000, "B": 2000, "C": 3000, "D": 4000},
    },
    "family_track": {
        "children_threshold": 4,
        "employer_share_percent": 50,
        "employee_share_percent": 50,
    },
    "ev_refund": {"amount": 100, "mode": "net"},
    "unused_budget": "not_paid",
    "flags": {
        "participation_reduces_usage_value": False,
        "variable_pay_in_contribution_base": False,
        "car_maintenance_outside_benefits_base": True,
    },
    "payroll": {
        "net_pay_method": "monthly",
        "income_tax": {
            "source": SOURCE,
            "brackets": [
                {"above": 0, "up_to": 100, "rate_percent": 10},
                {"above": 100, "up_to": None, "rate_percent": 20},
            ],
        },
        "credit_point": {"value": 200, "source": SOURCE},
        "social_insurance": {
            "source": SOURCE,
            "reduced_rate_threshold": 1000,
            "max_insurable_income": 9000,
            "national_insurance": {"reduced_rate_percent": 1, "full_rate_percent": 7},
            "health_tax": {"reduced_rate_percent": 3, "full_rate_percent": 5},
        },
        "pension_credit": {
            "source": SOURCE,
            "rate_percent": 35,
            "qualifying_share_percent": 7,
            "income_ceiling": 2000,
        },
        "study_fund": {
            "source": SOURCE,
            "salary_ceiling": 3000,
            "exempt_employer_rate_percent": 7,
            "employer_rate_percent": 7,
        },
    },
    "pending": [
        {"field": "ev_refund.mode", "default": "net", "question": "Net or gross-up?"},
        {
            "field": "flags.participation_reduces_usage_value",
            "default": False,
            "question": "Does participation reduce the usage value?",
        },
    ],
}
RULES = RulesConfig.model_validate(RULES_DOCUMENT)
THRESHOLD = RULES.family_track.children_threshold


def make_key(model_code: str) -> CarKey:
    return CarKey.parse(f"2026-2026-1-01229-{model_code}")


CAR_KEYS = {
    Group.A: make_key("00001"),
    Group.B: make_key("00002"),
    Group.C: make_key("00003"),
    Group.D: make_key("00004"),
}
FAMILY_KEY = make_key("00005")
NIRO_EV_KEY = make_key("00006")
UNLISTED_KEY = make_key("00099")


def catalog_car(
    name: str, key: CarKey, group: Group, *, family_track: bool = False
) -> dict[str, Any]:
    return {
        "display_name": name,
        "official_key": str(key),
        "monthly_cost": 3000,
        "group": group,
        "family_track": family_track,
    }


CATALOG = CarCatalog.model_validate(
    {
        "cars": [
            *(catalog_car(f"Car {group}", key, group) for group, key in CAR_KEYS.items()),
            catalog_car("Family car", FAMILY_KEY, Group.A, family_track=True),
            catalog_car("Niro EV", NIRO_EV_KEY, Group.C),
        ]
    }
)

OCCUPATIONS = OccupationsTable.model_validate(
    {
        "occupations": [
            {
                "name": "Office administration",
                "group": "B",
                "car_mandatory": False,
                "car_waiver_allowed": True,
                "car_maintenance": 1500,
            },
            {
                "name": " Field sales ",
                "group": "B",
                "car_mandatory": True,
                "car_waiver_allowed": False,
                "car_maintenance": 0,
            },
            {
                "name": "Division manager",
                "group": "D",
                "car_mandatory": False,
                "car_waiver_allowed": True,
                "car_maintenance": 3000,
            },
        ]
    }
)


def make_record(key: CarKey, powertrain: Powertrain, trade_name: str = "MODEL") -> CarRecord:
    return CarRecord(
        key=key,
        manufacturer_name="MAKER",
        trade_name=trade_name,
        model_description="DESCRIPTION",
        engine_cc=1600,
        powertrain=powertrain,
        list_price=150_000,
        adjusted_price=150_000,
        usage_value=3_000,
    )


def check_car(
    key: CarKey,
    profile: EmployeeProfile,
    *,
    powertrain: Powertrain | None = Powertrain.CONVENTIONAL,
    trade_name: str = "MODEL",
    rules: RulesConfig = RULES,
) -> EligibilityResult:
    # powertrain=None stands for a car whose dataset record is absent.
    record = None if powertrain is None else make_record(key, powertrain, trade_name)
    return check_eligibility(
        profile, CarAlternative(car_key=key), rules, CATALOG, OCCUPATIONS, record
    )


def check_waiver(occupation: str | None) -> EligibilityResult:
    return check_eligibility(
        EmployeeProfile(occupation=occupation),
        WaiverAlternative(),
        RULES,
        CATALOG,
        OCCUPATIONS,
        None,
    )


@pytest.mark.parametrize(
    ("employee_group", "car_group", "status"),
    [
        pytest.param(Group.D, Group.C, "eligible", id="higher-group-takes-a-lower-group-car"),
        pytest.param(Group.C, Group.C, "eligible", id="same-group"),
        pytest.param(Group.C, Group.D, "not_eligible", id="lower-group-takes-a-higher-group-car"),
        pytest.param(Group.A, Group.B, "not_eligible", id="lowest-group-takes-the-next-group-car"),
    ],
)
def test_regular_track_follows_the_group_order(
    employee_group: Group, car_group: Group, status: str
) -> None:
    result = check_car(CAR_KEYS[car_group], EmployeeProfile(eligibility_group=employee_group))

    allowed = status == "eligible"
    assert result.status == status
    assert result.missing_fields == ()
    assert result.reason == copy_he.car_group_reason(car_group, employee_group, allowed=allowed)


def test_regular_track_uses_the_group_order_of_the_rules_not_the_alphabet() -> None:
    document = copy.deepcopy(RULES_DOCUMENT)
    document["groups"]["order"] = ["D", "C", "B", "A"]
    reversed_rules = RulesConfig.model_validate(document)

    lowest = EmployeeProfile(eligibility_group=Group.D)
    highest = EmployeeProfile(eligibility_group=Group.A)

    assert check_car(CAR_KEYS[Group.C], lowest, rules=reversed_rules).status == "not_eligible"
    assert check_car(CAR_KEYS[Group.D], highest, rules=reversed_rules).status == "eligible"


@pytest.mark.parametrize(
    ("children", "status"),
    [
        pytest.param(THRESHOLD + 1, "eligible", id="above-the-threshold"),
        pytest.param(THRESHOLD, "eligible", id="at-the-threshold"),
        pytest.param(THRESHOLD - 1, "not_eligible", id="below-the-threshold"),
        pytest.param(0, "not_eligible", id="no-children"),
    ],
)
def test_family_track_follows_the_children_threshold(children: int, status: str) -> None:
    result = check_car(FAMILY_KEY, EmployeeProfile(children_under_23=children))

    allowed = status == "eligible"
    assert result.status == status
    assert result.missing_fields == ()
    assert result.reason == copy_he.family_track_reason(children, THRESHOLD, allowed=allowed)


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        pytest.param(EmployeeProfile(occupation="Office administration"), Group.B, id="occupation"),
        pytest.param(EmployeeProfile(occupation=" Division manager "), Group.D, id="padded"),
        pytest.param(
            EmployeeProfile(occupation="Office administration", eligibility_group=Group.D),
            Group.D,
            id="override-wins",
        ),
        pytest.param(EmployeeProfile(eligibility_group=Group.C), Group.C, id="override-alone"),
        pytest.param(EmployeeProfile(occupation="Gardener"), None, id="unlisted-occupation"),
        pytest.param(EmployeeProfile(), None, id="no-occupation"),
    ],
)
def test_the_employee_group_is_the_override_else_the_occupations_group(
    profile: EmployeeProfile, expected: Group | None
) -> None:
    assert employee_group(profile, OCCUPATIONS) == expected


@pytest.mark.parametrize(
    ("occupation", "car_group", "status"),
    [
        pytest.param("Office administration", Group.B, "eligible", id="b-takes-b"),
        pytest.param("Office administration", Group.C, "not_eligible", id="b-refused-c"),
        pytest.param("Division manager", Group.D, "eligible", id="d-takes-d"),
    ],
)
def test_a_regular_car_is_decided_by_the_group_of_the_occupation(
    occupation: str, car_group: Group, status: str
) -> None:
    result = check_car(CAR_KEYS[car_group], EmployeeProfile(occupation=occupation))

    assert result.status == status


def test_an_unlisted_occupation_leaves_a_regular_car_missing_the_occupation() -> None:
    result = check_car(CAR_KEYS[Group.A], EmployeeProfile(occupation="Gardener"))

    assert result.status == "missing"
    assert result.missing_fields == (MissingField.OCCUPATION,)


def test_each_track_needs_only_its_own_profile_field() -> None:
    family = check_car(FAMILY_KEY, EmployeeProfile(children_under_23=THRESHOLD))
    regular = check_car(CAR_KEYS[Group.A], EmployeeProfile(eligibility_group=Group.A))

    assert family.status == "eligible"
    assert regular.status == "eligible"


@pytest.mark.parametrize(
    ("occupation", "status"),
    [
        pytest.param("Office administration", "eligible", id="allowed"),
        pytest.param("  Office administration\t", "eligible", id="padded-profile-value"),
        pytest.param("Field sales", "not_eligible", id="not-allowed-padded-table-name"),
    ],
)
def test_waiver_follows_the_occupations_table(occupation: str, status: str) -> None:
    result = check_waiver(occupation)

    allowed = status == "eligible"
    assert result.status == status
    assert result.missing_fields == ()
    assert result.is_electric is None
    assert result.reason == copy_he.waiver_reason(occupation.strip(), allowed=allowed)


def test_an_occupation_missing_from_the_profile_and_one_missing_from_the_table_differ() -> None:
    from_profile = check_waiver(None)
    from_table = check_waiver("Gardener")

    for result in (from_profile, from_table):
        assert result.status == "missing"
        assert result.missing_fields == (MissingField.OCCUPATION,)
        assert result.is_electric is None
        assert copy_he.MISSING_FIELD_LABELS[MissingField.OCCUPATION] in result.reason
    assert from_profile.reason == copy_he.occupation_not_provided()
    assert from_table.reason == copy_he.occupation_not_in_table("Gardener")
    assert from_profile.reason != from_table.reason


@pytest.mark.parametrize("occupation", ["", "  \t "])
def test_a_blank_occupation_counts_as_missing_from_the_profile(occupation: str) -> None:
    assert check_waiver(occupation) == check_waiver(None)


def test_a_car_outside_the_catalog_is_missing_its_car_group() -> None:
    profile = EmployeeProfile(eligibility_group=Group.D, children_under_23=5)

    result = check_car(UNLISTED_KEY, profile)

    assert result.status == "missing"
    assert result.missing_fields == (MissingField.CAR_GROUP,)
    assert result.reason == copy_he.missing_data(result.missing_fields)


def test_an_absent_record_is_missing_its_powertrain() -> None:
    profile = EmployeeProfile(eligibility_group=Group.D)

    result = check_car(CAR_KEYS[Group.A], profile, powertrain=None)

    assert result.status == "missing"
    assert result.missing_fields == (MissingField.POWERTRAIN,)
    assert result.is_electric is None
    assert result.reason == copy_he.missing_data(result.missing_fields)


@pytest.mark.parametrize(
    ("key", "profile", "powertrain", "expected"),
    [
        pytest.param(
            CAR_KEYS[Group.A],
            EmployeeProfile(),
            None,
            (MissingField.POWERTRAIN, MissingField.OCCUPATION),
            id="regular-track-without-record-or-occupation",
        ),
        pytest.param(
            FAMILY_KEY,
            EmployeeProfile(),
            None,
            (MissingField.POWERTRAIN, MissingField.CHILDREN_UNDER_23),
            id="family-track-without-record-or-children",
        ),
        pytest.param(
            UNLISTED_KEY,
            EmployeeProfile(),
            None,
            (MissingField.CAR_GROUP, MissingField.POWERTRAIN),
            id="outside-the-catalog-without-record",
        ),
        pytest.param(
            UNLISTED_KEY,
            EmployeeProfile(),
            Powertrain.CONVENTIONAL,
            (MissingField.CAR_GROUP,),
            id="outside-the-catalog-track-unknown",
        ),
        pytest.param(
            CAR_KEYS[Group.A],
            EmployeeProfile(children_under_23=5),
            Powertrain.CONVENTIONAL,
            (MissingField.OCCUPATION,),
            id="regular-track-needs-no-children",
        ),
        pytest.param(
            FAMILY_KEY,
            EmployeeProfile(eligibility_group=Group.D),
            Powertrain.CONVENTIONAL,
            (MissingField.CHILDREN_UNDER_23,),
            id="family-track-needs-no-group",
        ),
    ],
)
def test_every_missing_input_is_listed_and_nothing_is_decided(
    key: CarKey,
    profile: EmployeeProfile,
    powertrain: Powertrain | None,
    expected: tuple[MissingField, ...],
) -> None:
    result = check_car(key, profile, powertrain=powertrain)

    assert result.status == "missing"
    assert result.missing_fields == expected
    assert result.reason == copy_he.missing_data(expected)
    for field in expected:
        assert copy_he.MISSING_FIELD_LABELS[field] in result.reason


@pytest.mark.parametrize(
    ("powertrain", "is_electric"),
    [
        pytest.param(Powertrain.ELECTRIC, True, id="electric"),
        pytest.param(Powertrain.HYBRID, False, id="hybrid"),
        pytest.param(Powertrain.PLUG_IN, False, id="plug-in"),
        pytest.param(Powertrain.CONVENTIONAL, False, id="conventional"),
    ],
)
def test_a_car_is_electric_only_when_its_record_says_electric(
    powertrain: Powertrain, is_electric: bool
) -> None:
    profile = EmployeeProfile(eligibility_group=Group.A)

    result = check_car(CAR_KEYS[Group.A], profile, powertrain=powertrain)

    assert result.is_electric is is_electric


def test_a_car_named_ev_with_a_hybrid_record_is_not_electric() -> None:
    profile = EmployeeProfile(eligibility_group=Group.D)

    result = check_car(NIRO_EV_KEY, profile, powertrain=Powertrain.HYBRID, trade_name="NIRO EV")

    assert result.status == "eligible"
    assert result.is_electric is False


@pytest.mark.parametrize(
    ("key", "profile", "status"),
    [
        pytest.param(
            CAR_KEYS[Group.A], EmployeeProfile(eligibility_group=Group.D), "eligible", id="eligible"
        ),
        pytest.param(
            CAR_KEYS[Group.D],
            EmployeeProfile(eligibility_group=Group.A),
            "not_eligible",
            id="not-eligible",
        ),
        pytest.param(UNLISTED_KEY, EmployeeProfile(), "missing", id="missing"),
    ],
)
def test_electric_is_reported_whatever_the_status(
    key: CarKey, profile: EmployeeProfile, status: str
) -> None:
    result = check_car(key, profile, powertrain=Powertrain.ELECTRIC)

    assert result.status == status
    assert result.is_electric is True


def test_the_result_is_repeatable_and_the_inputs_stay_unchanged() -> None:
    inputs = (
        EmployeeProfile(eligibility_group=Group.C),
        CarAlternative(car_key=CAR_KEYS[Group.D]),
        RULES,
        CATALOG,
        OCCUPATIONS,
        make_record(CAR_KEYS[Group.D], Powertrain.ELECTRIC),
    )
    snapshot = copy.deepcopy(inputs)

    first = check_eligibility(*inputs)
    second = check_eligibility(*inputs)

    assert first == second
    assert inputs == snapshot


def test_every_missing_field_has_its_own_hebrew_label() -> None:
    labels = copy_he.MISSING_FIELD_LABELS

    assert set(labels) == set(MissingField)
    assert all(label.strip() for label in labels.values())
    assert len(set(labels.values())) == len(labels)


def test_group_letters_follow_the_hebrew_alphabet() -> None:
    alef, bet, gimel, dalet = "\u05d0", "\u05d1", "\u05d2", "\u05d3"

    assert [copy_he.GROUP_LETTERS[group] for group in Group] == [alef, bet, gimel, dalet]


def test_reason_templates_carry_their_values_and_tell_the_verdicts_apart() -> None:
    group_pairs = {
        copy_he.car_group_reason(car, employee, allowed=True) for car in Group for employee in Group
    }
    family = copy_he.family_track_reason(7, 9, allowed=True)

    assert len(group_pairs) == len(Group) ** 2
    assert "7" in family
    assert "9" in family
    assert "Gardener" in copy_he.waiver_reason("Gardener", allowed=True)
    assert "Gardener" in copy_he.occupation_not_in_table("Gardener")

    verdicts = [
        copy_he.car_group_reason(Group.C, Group.D, allowed=allowed) for allowed in (True, False)
    ]
    verdicts += [copy_he.family_track_reason(7, 9, allowed=allowed) for allowed in (True, False)]
    verdicts += [copy_he.waiver_reason("Gardener", allowed=allowed) for allowed in (True, False)]
    assert len(set(verdicts)) == len(verdicts)
