import re
from collections.abc import Iterator, Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from car_advisor.agent.tools import register_tools
from car_advisor.engine.comparison import ComparisonInputs, compare
from car_advisor.engine.money import display
from car_advisor.engine.payroll import PayrollBreakdown, PayrollInput, PayrollOk, calculate_net_pay
from car_advisor.engine.types import (
    CarAlternative,
    CarKey,
    CarRecord,
    EmployeeProfile,
    Group,
    Powertrain,
    RepParticipation,
)
from car_advisor.rules.loader import AppConfig, load_config
from car_advisor.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
EXPLANATIONS = ROOT / "docs" / "explanations.he.md"
RULES = ROOT / "config" / "rules.yaml"

D = Decimal
LRM = "\u200e"


def read(path: Path) -> str:
    # The direction marks around a sign are for the eye only.
    return path.read_text(encoding="utf-8").replace(LRM, "")


def dotted_keys(node: Mapping[str, Any], prefix: str = "") -> Iterator[str]:
    """The keys of a YAML mapping as dotted paths. A list or a scalar ends a path."""
    for key, value in node.items():
        if isinstance(value, Mapping):
            yield from dotted_keys(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}"


def section(text: str, heading: str) -> str:
    """The text from a heading to the next heading of the same level."""
    start = text.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    following = re.search(rf"^#{{1,{level}}} ", text[start + len(heading) :], re.MULTILINE)
    return text[start : start + len(heading) + following.start()] if following else text[start:]


def test_the_readme_names_the_check_command_the_quick_start_and_both_screens() -> None:
    text = read(README)

    for needed in (
        "./check.sh",
        "docker compose up",
        "cp .env.example .env",
        "http://127.0.0.1:8080/employee",
        "http://127.0.0.1:8080/rep",
        "http://127.0.0.1:8000/api/health",
    ):
        assert needed in text


def test_the_readme_says_where_each_configuration_file_and_the_explanations_are() -> None:
    text = read(README)

    for location in (
        ".env.example",
        "config/rules.yaml",
        "config/car_catalog.yaml",
        "config/occupations.yaml",
        "docs/explanations.he.md",
    ):
        assert location in text
    assert EXPLANATIONS.is_file()


def test_the_readme_names_the_tools_that_dev_mode_needs() -> None:
    text = read(README)

    for tool in ("uv", "Python 3.12", "Node.js 22", "Docker"):
        assert tool in text


def test_the_explanations_document_every_setting() -> None:
    text = read(EXPLANATIONS)

    missing = [name.upper() for name in Settings.model_fields if f"`{name.upper()}`" not in text]

    assert missing == []


def test_the_explanations_document_every_rules_field() -> None:
    text = read(EXPLANATIONS)
    keys = dotted_keys(yaml.safe_load(RULES.read_text(encoding="utf-8")))

    missing = [key for key in keys if f"`{key}`" not in text]

    assert missing == []


def test_the_explanations_give_each_rules_default_as_the_file_has_it() -> None:
    table = section(read(EXPLANATIONS), "### כללי החישוב (Rules_Config, הקובץ `config/rules.yaml`)")
    rules = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    rows = {
        cells[0].strip("` "): cells[3]
        for cells in (
            [cell.strip() for cell in line.strip("|").split("|")]
            for line in table.splitlines()
            if line.startswith("| `")
        )
    }

    def leaf(path: str) -> Any:
        node: Any = rules
        for part in path.split("."):
            node = node[part]
        return node

    for key in dotted_keys(rules):
        value = leaf(key)
        if isinstance(value, bool):
            assert rows[key] == str(value).lower(), key
        elif isinstance(value, int | float):
            assert rows[key].replace(",", "") == str(value), key


def test_the_explanations_name_the_six_agent_tools() -> None:
    class Collector:
        names: list[str]

        def __init__(self) -> None:
            self.names = []

        def tool(self, *args: Any, **kwargs: Any) -> Any:
            def register(function: Any) -> Any:
                self.names.append(function.__name__)
                return function

            return register(args[0]) if args and callable(args[0]) else register

    collector = Collector()
    register_tools(collector)  # type: ignore[arg-type]
    text = read(EXPLANATIONS)

    assert len(collector.names) == 6
    assert [name for name in collector.names if f"`{name}`" not in text] == []


def test_every_pending_item_of_the_rules_is_an_open_question_with_its_default() -> None:
    rules = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    questions = section(read(EXPLANATIONS), "## 11. שאלות פתוחות לשטראוס")

    for item in rules["pending"]:
        row = next(line for line in questions.splitlines() if f"`{item['field']}`" in line)
        assert str(item["default"]).lower() in row.lower(), item["field"]


def test_the_open_questions_ask_for_the_real_catalog_with_the_vehicle_type_code() -> None:
    questions = section(read(EXPLANATIONS), "## 11. שאלות פתוחות לשטראוס")

    assert "config/car_catalog.yaml" in questions
    assert "config/occupations.yaml" in questions
    assert "backend/tests/fixtures/challenge/" in questions
    assert "קוד סוג רכב" in questions


def test_the_submission_summary_has_its_three_parts_and_says_the_document_reader_is_out() -> None:
    text = read(EXPLANATIONS)
    summary = section(text, "## 10. סיכום להגשה")

    for part in ("### מה עובד בגרסה המוגשת", "### מגבלות", "### מה נדרש לפיילוט"):
        assert part in summary
    assert "קורא המסמכים" in summary
    assert "לא כלול" in summary


# The worked example of the explanations is employee E-001 of the challenge, who moves from the
# Corolla Cross to the BYD Atto 3. The participation of the new car is the catalog's, as the answer
# of the representative, and the nets are those of the challenge's sheet.
TODAY_KEY = CarKey.parse("2026-2025-1-00413-00296")
EV_KEY = CarKey.parse("2026-2025-1-01014-00003")
TODAY_USAGE_VALUE = 4070
EV_USAGE_VALUE = 3130
TODAY_PARTICIPATION = 300
EV_PARTICIPATION = 300
SHEET_NET_PAYS = (11032, 11693)

# The label of each row of the table, and the component of the breakdown that it shows.
ROWS = {
    "שכר ברוטו": "gross_salary",
    "שווי שימוש חייב": "taxable_usage_value",
    "עודף קרן השתלמות חייב": "study_fund_taxable_excess",
    "הכנסה חייבת": "taxable_income",
    "מס לפני זיכויים": "tax_before_credits",
    "זיכוי נקודות זיכוי": "credit_points_credit",
    "זיכוי פנסיה": "pension_credit",
    "מס הכנסה": "income_tax",
    "ביטוח לאומי": "national_insurance",
    "מס בריאות": "health_tax",
    "הפרשת פנסיה": "pension_contribution",
    "הפרשת קרן השתלמות": "study_fund_contribution",
    "השתתפות": "participation",
    "החזר חשמל": "ev_refund",
    "אחזקת רכב": "car_maintenance",
    "**שכר נטו**": "net_pay",
}


@pytest.fixture(scope="module")
def config() -> AppConfig:
    return load_config(RULES, ROOT / "config/car_catalog.yaml", ROOT / "config/occupations.yaml")


def payroll(
    config: AppConfig, usage_value: int, participation: int, ev_refund: int
) -> PayrollBreakdown:
    result = calculate_net_pay(
        PayrollInput(
            base_salary=D(18500),
            commissions=(),
            bonuses=(),
            pension_rate_percent=D(6),
            study_fund_rate_percent=D("2.5"),
            credit_points=D("2.75"),
            usage_value=D(usage_value),
            participation=D(participation),
            ev_refund=D(ev_refund),
            car_maintenance=D(0),
        ),
        config.rules,
    )
    assert isinstance(result, PayrollOk)
    return result.breakdown


def row_of(text: str, label: str) -> list[str]:
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if line.startswith("|") and cells[0] == label and len(cells) >= 4:
            return cells
    raise AssertionError(f"no row for {label}")


def shekels(value: Decimal) -> str:
    return f"₪{display(value):,}"


def test_the_worked_example_shows_what_the_payroll_calculator_returns(config: AppConfig) -> None:
    text = section(read(EXPLANATIONS), "### דוגמה מחושבת")
    today = payroll(config, TODAY_USAGE_VALUE, TODAY_PARTICIPATION, 0)
    electric = payroll(config, EV_USAGE_VALUE, EV_PARTICIPATION, 250)

    for label, field in ROWS.items():
        cells = row_of(text, label)
        for cell, breakdown in ((cells[1], today), (cells[2], electric)):
            value: Decimal = getattr(breakdown, field)
            shown = cell.replace("**", "").replace(",", "")
            assert shown.startswith(shekels(value).replace(",", "")), label
            exact = re.search(r"\(([\d.]+)\)", shown)
            if value != display(value):
                assert exact is not None and D(exact.group(1)) == value, label
            else:
                assert exact is None, label


def test_the_worked_example_shows_the_difference_the_parts_and_the_explanation(
    config: AppConfig,
) -> None:
    text = section(read(EXPLANATIONS), "### דוגמה מחושבת")

    def record(key: CarKey, powertrain: Powertrain, usage_value: int) -> CarRecord:
        return CarRecord(
            key=key,
            manufacturer_name="MAKER",
            trade_name="MODEL",
            model_description="DESCRIPTION",
            engine_cc=1600,
            powertrain=powertrain,
            list_price=150_000,
            adjusted_price=150_000,
            usage_value=usage_value,
        )

    profile = EmployeeProfile(
        eligibility_group=Group.C,
        base_salary=D(18500),
        pay_components=[],
        pension_rate_percent=D(6),
        study_fund_rate_percent=D("2.5"),
        credit_points=D("2.75"),
        has_current_car=True,
        current_car_key=TODAY_KEY,
        current_car_participation=D(TODAY_PARTICIPATION),
    )
    inputs = ComparisonInputs(
        rules=config.rules,
        catalog=config.catalog,
        occupations=config.occupations,
        records={
            TODAY_KEY: record(TODAY_KEY, Powertrain.HYBRID, TODAY_USAGE_VALUE),
            EV_KEY: record(EV_KEY, Powertrain.ELECTRIC, EV_USAGE_VALUE),
        },
        rep_participations={
            EV_KEY: RepParticipation(amount=D(EV_PARTICIPATION), effective_date=date(2026, 1, 1))
        },
        rep_questions=frozenset(),
    )

    comparison = compare(profile, [CarAlternative(car_key=EV_KEY)], inputs)
    column = comparison.alternatives[0]

    assert comparison.today.net_pay is not None
    assert column.net_pay is not None
    assert column.difference is not None
    assert column.breakdown is not None
    assert column.explanation is not None
    assert (comparison.today.net_pay.shown, column.net_pay.shown) == SHEET_NET_PAYS
    assert comparison.today.net_pay.shown == display(
        payroll(config, TODAY_USAGE_VALUE, TODAY_PARTICIPATION, 0).net_pay
    )
    assert f"+₪{column.difference.shown:,}" in text
    assert f"{column.difference.exact.normalize():f}" in text
    usage_label = f"שווי שימוש (מ\u05be{TODAY_USAGE_VALUE:,} ל\u05be{EV_USAGE_VALUE:,})"
    participation_label = f"השתתפות (מ\u05be{TODAY_PARTICIPATION} ל\u05be{EV_PARTICIPATION})"
    parts = {
        usage_label: column.breakdown.usage_value,
        "החזר חשמל": column.breakdown.ev_refund,
        participation_label: column.breakdown.participation,
        "אחזקת רכב": column.breakdown.car_maintenance,
    }
    breakdown_table = text[text.index("**פירוק ההפרש") :]
    for label, amount in parts.items():
        line = next(
            line for line in breakdown_table.splitlines() if line.startswith(f"| {label} |")
        )
        cell = line.strip("|").split("|")[1].strip()
        sign = "+" if amount.shown > 0 else "\u2212" if amount.shown < 0 else ""
        assert cell == f"{sign}₪{abs(amount.shown):,}", label
    for line in column.explanation:
        assert line in text
