from decimal import Decimal
from functools import reduce
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from car_advisor.engine.config import RulesConfig
from car_advisor.engine.types import Group
from car_advisor.rules.loader import load_config
from tests.engine.test_config import CATALOG, OCCUPATIONS

RULES_FILE = Path(__file__).resolve().parents[3] / "config" / "rules.yaml"

CHALLENGE_FILE_GROUPS = ["income_tax", "credit_point", "social_insurance", "pension_credit"]


@pytest.fixture(scope="module")
def rules(tmp_path_factory: pytest.TempPathFactory) -> RulesConfig:
    directory = tmp_path_factory.mktemp("config")
    catalog = directory / "car_catalog.yaml"
    occupations = directory / "occupations.yaml"
    catalog.write_text(yaml.safe_dump(CATALOG, sort_keys=False), encoding="utf-8")
    occupations.write_text(yaml.safe_dump(OCCUPATIONS, sort_keys=False), encoding="utf-8")
    return load_config(RULES_FILE, catalog, occupations).rules


def current_value(rules: RulesConfig, field: str) -> object:
    return reduce(getattr, field.split("."), rules)


def test_the_tracked_file_loads_through_the_loader(rules: RulesConfig) -> None:
    assert isinstance(rules, RulesConfig)


def test_group_budgets_are_the_monthly_car_budgets(rules: RulesConfig) -> None:
    assert rules.groups.budgets == {Group.A: 3500, Group.B: 3700, Group.C: 4500, Group.D: 5200}


def test_group_order_runs_from_lowest_to_highest(rules: RulesConfig) -> None:
    assert rules.groups.order == [Group.A, Group.B, Group.C, Group.D]


def test_family_track_defaults(rules: RulesConfig) -> None:
    track = rules.family_track

    assert track.children_threshold == 4
    assert (track.employer_share_percent, track.employee_share_percent) == (50, 50)


def test_ev_refund_is_250_added_to_net_pay(rules: RulesConfig) -> None:
    assert rules.ev_refund.amount == 250
    assert rules.ev_refund.mode == "net"


def test_unused_budget_is_not_paid(rules: RulesConfig) -> None:
    assert rules.unused_budget == "not_paid"


def test_flags_hold_their_defaults(rules: RulesConfig) -> None:
    assert rules.flags.model_dump() == {
        "participation_reduces_usage_value": False,
        "variable_pay_in_contribution_base": True,
        "car_maintenance_outside_benefits_base": True,
    }


def test_net_pay_is_calculated_monthly(rules: RulesConfig) -> None:
    assert rules.payroll.net_pay_method == "monthly"


def test_income_tax_brackets_are_the_challenges_monthly_brackets(rules: RulesConfig) -> None:
    brackets = rules.payroll.income_tax.brackets

    assert [(item.above, item.up_to, item.rate_percent) for item in brackets] == [
        (0, 7010, 10),
        (7010, 10060, 14),
        (10060, 16150, 20),
        (16150, 22440, 31),
        (22440, 46690, 35),
        (46690, 60130, 47),
        (60130, None, 50),
    ]


def test_credit_point_value(rules: RulesConfig) -> None:
    assert rules.payroll.credit_point.value == 242


def test_social_insurance_rates_and_thresholds(rules: RulesConfig) -> None:
    insurance = rules.payroll.social_insurance

    assert insurance.reduced_rate_threshold == 7522
    assert insurance.max_insurable_income == 50695
    assert insurance.national_insurance.model_dump() == {
        "reduced_rate_percent": Decimal("1.04"),
        "full_rate_percent": 7,
    }
    assert insurance.health_tax.model_dump() == {
        "reduced_rate_percent": Decimal("3.23"),
        "full_rate_percent": Decimal("5.17"),
    }


def test_pension_credit_rate_share_and_ceiling(rules: RulesConfig) -> None:
    credit = rules.payroll.pension_credit

    assert credit.rate_percent == 35
    assert credit.qualifying_share_percent == 7
    assert credit.income_ceiling == 9500


def test_the_employer_study_fund_deposit_is_not_added_to_taxable_pay(rules: RulesConfig) -> None:
    fund = rules.payroll.study_fund

    assert fund.salary_ceiling == 15712
    assert fund.exempt_employer_rate_percent == Decimal("7.5")
    assert fund.employer_rate_percent == 0


@pytest.mark.parametrize("group", CHALLENGE_FILE_GROUPS)
def test_the_payroll_parameter_groups_of_the_challenge_name_its_file(
    rules: RulesConfig, group: str
) -> None:
    source = getattr(rules.payroll, group).source

    assert "strauss-rules.json" in source
    assert "mock-2026-10-12" in source


def test_the_study_fund_cites_the_public_page_of_its_ceiling(rules: RulesConfig) -> None:
    url = urlsplit(rules.payroll.study_fund.source)

    assert (url.scheme, url.netloc) == ("https", "www.kolzchut.org.il")
    assert url.path.strip("/")


def test_pending_lists_the_open_questions_with_their_defaults(rules: RulesConfig) -> None:
    assert {item.field: item.default for item in rules.pending} == {
        "payroll.credit_point.value": 242,
        "payroll.study_fund.employer_rate_percent": 0,
    }
    assert all(item.question for item in rules.pending)


def test_every_pending_item_resolves_to_its_current_value(rules: RulesConfig) -> None:
    for item in rules.pending:
        value = current_value(rules, item.field)

        assert (type(item.default), item.default) == (type(value), value), item.field
