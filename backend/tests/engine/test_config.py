import copy
from decimal import Decimal
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from car_advisor.engine.config import CarCatalog, OccupationsTable, RulesConfig
from car_advisor.engine.types import CarKey, Group

SOURCE = "https://example.org/rates"

PENDING: list[dict[str, Any]] = [
    {"field": "ev_refund.mode", "default": "net", "question": "Net or gross-up?"},
    {"field": "payroll.credit_point.value", "default": 200, "question": "Which tables?"},
    {
        "field": "payroll.study_fund.employer_rate_percent",
        "default": 7.5,
        "question": "Is the deposit taxed?",
    },
    {
        "field": "flags.participation_reduces_usage_value",
        "default": False,
        "question": "Does participation reduce the usage value?",
    },
]

RULES: dict[str, Any] = {
    "groups": {
        "order": ["A", "B", "C", "D"],
        "budgets": {"A": 1000, "B": 2000, "C": 3000, "D": 4000},
    },
    "family_track": {
        "children_threshold": 3,
        "employer_share_percent": 60,
        "employee_share_percent": 40,
    },
    "ev_refund": {"amount": 100, "mode": "net"},
    "unused_budget": "not_paid",
    "flags": {
        "participation_reduces_usage_value": False,
        "variable_pay_in_contribution_base": True,
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
            "national_insurance": {"reduced_rate_percent": 1.04, "full_rate_percent": 7},
            "health_tax": {"reduced_rate_percent": 3.23, "full_rate_percent": 5.17},
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
            "exempt_employer_rate_percent": 7.5,
            "employer_rate_percent": 7.5,
        },
    },
    "pending": PENDING,
}

DELETE = object()


def changed(document: dict[str, Any], path: str, value: object) -> dict[str, Any]:
    result = copy.deepcopy(document)
    *parents, last = path.split(".")
    node: Any = result
    for part in parents:
        node = node[int(part)] if isinstance(node, list) else node[part]
    key: Any = int(last) if isinstance(node, list) else last
    if value is DELETE:
        del node[key]
    else:
        node[key] = value
    return result


def error_paths(model: type[BaseModel], document: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(document)
    return [".".join(str(part) for part in error["loc"]) for error in caught.value.errors()]


def test_a_minimal_valid_rules_config_passes() -> None:
    rules = RulesConfig.model_validate(RULES)

    assert rules.groups.order == [Group.A, Group.B, Group.C, Group.D]
    assert rules.groups.budgets[Group.C] == Decimal(3000)
    assert rules.family_track.children_threshold == 3
    assert rules.payroll.social_insurance.national_insurance.reduced_rate_percent == Decimal("1.04")
    assert rules.payroll.income_tax.brackets[-1].up_to is None


def test_any_group_order_and_extra_pending_values_pass() -> None:
    document = changed(RULES, "groups.order", ["B", "D", "A", "C"])
    document["pending"].append(
        {"field": "groups.budgets.C", "default": "3000.00", "question": "Is the C budget final?"}
    )

    rules = RulesConfig.model_validate(document)

    assert rules.groups.order == [Group.B, Group.D, Group.A, Group.C]


def test_an_empty_pending_list_passes() -> None:
    rules = RulesConfig.model_validate(changed(RULES, "pending", []))

    assert rules.pending == []


BRACKETS = "payroll.income_tax.brackets"


@pytest.mark.parametrize(
    ("path", "value", "error_path"),
    [
        pytest.param("groups.budgets.B", -1, "groups.budgets.B", id="negative-budget"),
        pytest.param("ev_refund.amount", -0.01, "ev_refund.amount", id="negative-amount"),
        pytest.param(
            "payroll.social_insurance.health_tax.full_rate_percent",
            100.01,
            "payroll.social_insurance.health_tax.full_rate_percent",
            id="percent-above-100",
        ),
        pytest.param(
            "payroll.pension_credit.rate_percent",
            -1,
            "payroll.pension_credit.rate_percent",
            id="negative-percent",
        ),
        pytest.param("family_track.employee_share_percent", 50, "family_track", id="shares-110"),
        pytest.param(
            "payroll.social_insurance.reduced_rate_threshold",
            9001,
            "payroll.social_insurance",
            id="threshold-above-ceiling",
        ),
        pytest.param(
            "family_track.children_threshold",
            0,
            "family_track.children_threshold",
            id="threshold-0",
        ),
        pytest.param(
            "family_track.children_threshold",
            3.0,
            "family_track.children_threshold",
            id="threshold-float",
        ),
        pytest.param(
            "family_track.children_threshold",
            "3",
            "family_track.children_threshold",
            id="threshold-text",
        ),
        pytest.param("groups.order", ["A", "B", "B", "D"], "groups.order", id="order-repeats"),
        pytest.param("groups.order", ["A", "B", "C"], "groups.order", id="order-misses-a-group"),
        pytest.param("groups.order", ["A", "B", "C", "E"], "groups.order.3", id="order-unknown"),
        pytest.param("groups.budgets.D", DELETE, "groups.budgets", id="budget-missing"),
        pytest.param(f"{BRACKETS}.0.above", 1, BRACKETS, id="first-bracket-not-at-0"),
        pytest.param(f"{BRACKETS}.1.above", 150, BRACKETS, id="bracket-gap"),
        pytest.param(f"{BRACKETS}.1.above", 50, BRACKETS, id="bracket-overlap"),
        pytest.param(
            f"{BRACKETS}.1",
            {"above": 100, "up_to": 50, "rate_percent": 20},
            f"{BRACKETS}.1",
            id="bracket-descending",
        ),
        pytest.param(f"{BRACKETS}.0.up_to", None, BRACKETS, id="open-bracket-not-last"),
        pytest.param(f"{BRACKETS}.1.up_to", 200, BRACKETS, id="last-bracket-closed"),
        pytest.param(BRACKETS, [], BRACKETS, id="no-brackets"),
        pytest.param("ev_refund.mode", "gross", "ev_refund.mode", id="unknown-ev-mode"),
        pytest.param("unused_budget", "paid", "unused_budget", id="unused-budget-paid"),
        pytest.param(
            "payroll.net_pay_method", "cumulative", "payroll.net_pay_method", id="cumulative"
        ),
        pytest.param(
            "payroll.credit_point.source", "", "payroll.credit_point.source", id="empty-source"
        ),
        pytest.param(
            "payroll.study_fund.source", "  ", "payroll.study_fund.source", id="blank-source"
        ),
        pytest.param(
            "payroll.income_tax.source", DELETE, "payroll.income_tax.source", id="missing-source"
        ),
        pytest.param("pending.0.default", 4500, "pending", id="pending-default-differs"),
        pytest.param("payroll.credit_point.value", 201, "pending", id="value-changed-alone"),
        pytest.param("pending.3.default", 0, "pending", id="pending-0-for-false"),
        pytest.param("pending.0.field", "family_track.price", "pending", id="pending-unknown"),
        pytest.param("pending.0.field", "family_track", "pending", id="pending-section"),
        pytest.param("pending.1.question", "", "pending.1.question", id="pending-no-question"),
        pytest.param(
            "flags.variable_pay_in_contribution_base",
            DELETE,
            "flags.variable_pay_in_contribution_base",
            id="missing-flag",
        ),
        pytest.param("flags.typo_flag", True, "flags.typo_flag", id="unknown-key"),
        pytest.param(f"{BRACKETS}.0.note", "x", f"{BRACKETS}.0.note", id="unknown-nested-key"),
    ],
)
def test_rules_reject_a_single_violation_at_its_path(
    path: str, value: object, error_path: str
) -> None:
    assert error_paths(RulesConfig, changed(RULES, path, value)) == [error_path]


def test_a_reduced_rate_threshold_above_the_insurable_ceiling_is_rejected_with_both_names() -> None:
    document = changed(RULES, "payroll.social_insurance.reduced_rate_threshold", 9001)

    with pytest.raises(ValidationError) as caught:
        RulesConfig.model_validate(document)

    [error] = caught.value.errors()
    assert error["msg"] == (
        "Value error, reduced_rate_threshold must not be greater than max_insurable_income"
    )


def test_a_reduced_rate_threshold_equal_to_the_insurable_ceiling_passes() -> None:
    document = changed(RULES, "payroll.social_insurance.reduced_rate_threshold", 9000)

    rules = RulesConfig.model_validate(document)

    assert rules.payroll.social_insurance.reduced_rate_threshold == Decimal(9000)


def car(name: str, group: str, model_code: str) -> dict[str, Any]:
    return {
        "display_name": name,
        "official_key": {
            "tax_year": 2026,
            "registration_year": 2026,
            "vehicle_type_code": "1",
            "manufacturer_code": "01229",
            "model_code": model_code,
        },
        "monthly_cost": 3000,
        "group": group,
    }


CATALOG: dict[str, Any] = {
    "mock_data": "MOCK DATA",
    "cars": [
        car("Car A", "A", "00001"),
        car("Car B", "B", "00002"),
        car("Car C", "C", "00003"),
        car("Car D", "D", "00004"),
    ],
}

ORDER = [Group.A, Group.B, Group.C, Group.D]


def names(cars: list[Any]) -> list[str]:
    return [item.display_name for item in cars]


def test_a_valid_catalog_passes_without_the_marker() -> None:
    catalog = CarCatalog.model_validate({"cars": CATALOG["cars"]})

    assert catalog.mock_data is None
    assert catalog.cars[0].monthly_cost == Decimal(3000)
    assert catalog.cars[0].family_track is False
    assert catalog.cars[0].default_participation is None


@pytest.mark.parametrize(
    ("group", "expected"),
    [
        pytest.param(Group.A, ["Car A"], id="lowest"),
        pytest.param(Group.C, ["Car A", "Car B", "Car C"], id="middle"),
        pytest.param(Group.D, ["Car A", "Car B", "Car C", "Car D"], id="highest"),
    ],
)
def test_cars_for_returns_the_group_and_the_lower_groups(group: Group, expected: list[str]) -> None:
    catalog = CarCatalog.model_validate(CATALOG)

    assert names(catalog.cars_for(group, ORDER)) == expected


def test_cars_for_follows_the_given_order() -> None:
    catalog = CarCatalog.model_validate(CATALOG)

    assert names(catalog.cars_for(Group.C, [Group.D, Group.C, Group.B, Group.A])) == [
        "Car C",
        "Car D",
    ]


def test_find_returns_the_car_with_the_key() -> None:
    catalog = CarCatalog.model_validate(CATALOG)

    found = catalog.find(CarKey.parse("2026-2026-1-01229-00002"))

    assert found is not None
    assert found.display_name == "Car B"
    assert catalog.find(CarKey.parse("2026-2026-1-01229-00009")) is None


@pytest.mark.parametrize(
    ("path", "value", "error_path"),
    [
        pytest.param("cars.3.official_key.model_code", "00002", "cars", id="duplicate-key"),
        pytest.param("cars.0.monthly_cost", 0, "cars.0.monthly_cost", id="zero-cost"),
        pytest.param(
            "cars.0.default_participation",
            -1,
            "cars.0.default_participation",
            id="negative-participation",
        ),
        pytest.param("cars.2.group", "E", "cars.2.group", id="unknown-group"),
        pytest.param("cars.1.display_name", DELETE, "cars.1.display_name", id="missing-name"),
        pytest.param("cars.1.display_name", " ", "cars.1.display_name", id="blank-name"),
        pytest.param(
            "cars.0.official_key.model_code",
            1,
            "cars.0.official_key.model_code",
            id="unquoted-code",
        ),
        pytest.param("cars.0.colour", "red", "cars.0.colour", id="unknown-key"),
        pytest.param("cars", [], "cars", id="no-cars"),
    ],
)
def test_catalog_rejects_a_single_violation_at_its_path(
    path: str, value: object, error_path: str
) -> None:
    assert error_paths(CarCatalog, changed(CATALOG, path, value)) == [error_path]


def test_catalog_names_every_car_with_a_repeated_key() -> None:
    document = changed(CATALOG, "cars.2.official_key.model_code", "00001")
    document = changed(document, "cars.3.official_key.model_code", "00001")

    with pytest.raises(ValidationError) as caught:
        CarCatalog.model_validate(document)

    message = caught.value.errors()[0]["msg"]
    assert "cars[2] (Car C) has the same official_key as cars[0] (Car A)" in message
    assert "cars[3] (Car D) has the same official_key as cars[0] (Car A)" in message


def test_catalog_yaml_rejects_an_unquoted_code() -> None:
    document = yaml.safe_load(
        """
        cars:
          - display_name: Car A
            official_key:
              tax_year: 2026
              registration_year: 2026
              vehicle_type_code: "1"
              manufacturer_code: "01229"
              model_code: 00001
            monthly_cost: 3000
            group: A
        """
    )

    assert error_paths(CarCatalog, document) == ["cars.0.official_key.model_code"]


OCCUPATIONS: dict[str, Any] = {
    "mock_data": "MOCK DATA",
    "occupations": [
        {"name": "Office administration", "car_waiver_allowed": True, "car_maintenance": 1500},
        {"name": " Field sales ", "car_waiver_allowed": False, "car_maintenance": 0},
    ],
}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("Office administration", "Office administration", id="exact"),
        pytest.param("  Office administration\t", "Office administration", id="padded-query"),
        pytest.param("Field sales", " Field sales ", id="padded-stored-name"),
    ],
)
def test_find_compares_occupation_names_after_trimming(name: str, expected: str) -> None:
    table = OccupationsTable.model_validate(OCCUPATIONS)

    found = table.find(name)

    assert found is not None
    assert found.name == expected


@pytest.mark.parametrize("name", ["field sales", "Field  sales", "Sales", ""])
def test_find_returns_none_for_an_unlisted_occupation(name: str) -> None:
    assert OccupationsTable.model_validate(OCCUPATIONS).find(name) is None


SLASH_OCCUPATIONS: dict[str, Any] = {
    "occupations": [
        {"name": "מפתח/ת תוכנה", "car_waiver_allowed": True, "car_maintenance": 1500},
        {"name": "אנליסט/ית נתונים", "car_waiver_allowed": True, "car_maintenance": 1500},
    ]
}


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        pytest.param("מפתח/ת תוכנה", "מפתח/ת תוכנה", id="as-written"),
        pytest.param("מפתח תוכנה", "מפתח/ת תוכנה", id="masculine"),
        pytest.param("מפתחת תוכנה", "מפתח/ת תוכנה", id="feminine"),
        pytest.param("  מפתחת תוכנה\t", "מפתח/ת תוכנה", id="padded"),
        pytest.param("אנליסט נתונים", "אנליסט/ית נתונים", id="masculine-of-a-longer-ending"),
        pytest.param("אנליסטית נתונים", "אנליסט/ית נתונים", id="feminine-of-a-longer-ending"),
    ],
)
def test_find_matches_both_genders_of_a_slash_name(typed: str, expected: str) -> None:
    found = OccupationsTable.model_validate(SLASH_OCCUPATIONS).find(typed)

    assert found is not None
    assert found.name == expected


@pytest.mark.parametrize("typed", ["מפתחת", "מפתח/ת", "מפתחות תוכנה", "תוכנה מפתחת", "מפתח  תוכנה"])
def test_find_does_not_match_a_part_or_another_word_of_a_slash_name(typed: str) -> None:
    assert OccupationsTable.model_validate(SLASH_OCCUPATIONS).find(typed) is None


@pytest.mark.parametrize(
    ("path", "value", "error_path"),
    [
        pytest.param(
            "occupations.1.name", "Office administration  ", "occupations", id="duplicate-name"
        ),
        pytest.param(
            "occupations.0.car_maintenance",
            -1,
            "occupations.0.car_maintenance",
            id="negative-maintenance",
        ),
        pytest.param(
            "occupations.1.car_waiver_allowed",
            DELETE,
            "occupations.1.car_waiver_allowed",
            id="missing-waiver",
        ),
        pytest.param("occupations.0.name", "", "occupations.0.name", id="empty-name"),
        pytest.param("occupations.0.notes", "x", "occupations.0.notes", id="unknown-key"),
        pytest.param("occupations", [], "occupations", id="no-occupations"),
    ],
)
def test_occupations_reject_a_single_violation_at_its_path(
    path: str, value: object, error_path: str
) -> None:
    assert error_paths(OccupationsTable, changed(OCCUPATIONS, path, value)) == [error_path]
