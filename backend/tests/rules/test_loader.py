from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

from car_advisor.engine.types import Group
from car_advisor.rules.loader import ConfigError, load_config
from tests.engine.test_config import CATALOG, OCCUPATIONS, RULES, changed


class Files(NamedTuple):
    rules: Path
    catalog: Path
    occupations: Path


def write_yaml(path: Path, document: object) -> None:
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


@pytest.fixture
def files(tmp_path: Path) -> Files:
    files = Files(
        rules=tmp_path / "rules.yaml",
        catalog=tmp_path / "car_catalog.yaml",
        occupations=tmp_path / "occupations.yaml",
    )
    write_yaml(files.rules, RULES)
    write_yaml(files.catalog, CATALOG)
    write_yaml(files.occupations, OCCUPATIONS)
    return files


def failure(files: Files) -> ConfigError:
    with pytest.raises(ConfigError) as caught:
        load_config(*files)
    return caught.value


def test_valid_files_load_into_one_config(files: Files) -> None:
    config = load_config(*files)

    assert config.rules.groups.budgets[Group.C] == Decimal(3000)
    assert [car.display_name for car in config.catalog.cars] == ["Car A", "Car B", "Car C", "Car D"]
    assert config.occupations.find("Field sales") is not None


@pytest.mark.parametrize("index", range(len(Files._fields)), ids=Files._fields)
def test_a_missing_file_is_reported(files: Files, index: int) -> None:
    missing = files[index]
    missing.unlink()

    error = failure(files)

    assert error.path == missing
    assert error.problems == [f"file not found: {missing}"]
    assert f"file not found: {missing}" in str(error)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        pytest.param(
            "groups:\n  order: [A, B, C, D]\nfamily_track: three: four\n",
            "line 3: mapping values are not allowed here",
            id="syntax",
        ),
        pytest.param(
            "groups:\n  order: \x07\n",
            "line 2: special characters are not allowed",
            id="control-character",
        ),
    ],
)
def test_a_yaml_error_is_reported_at_its_line(files: Files, text: str, problem: str) -> None:
    files.rules.write_text(text, encoding="utf-8")

    error = failure(files)

    assert error.path == files.rules
    assert error.problems == [problem]
    assert problem in str(error)


def test_a_rules_file_with_three_invalid_fields_lists_all_three(files: Files) -> None:
    document = changed(RULES, "groups.budgets.B", -1)
    document = changed(document, "payroll.income_tax.brackets.1.rate_percent", 101)
    document = changed(document, "unused_budget", "paid")
    write_yaml(files.rules, document)

    error = failure(files)

    assert error.path == files.rules
    assert error.problems == [
        "groups.budgets.B: Input should be greater than or equal to 0",
        "unused_budget: Input should be 'not_paid'",
        "payroll.income_tax.brackets[1].rate_percent: Input should be less than or equal to 100",
    ]
    assert all(problem in str(error) for problem in error.problems)


def test_a_catalog_with_two_invalid_cars_names_both(files: Files) -> None:
    document = changed(CATALOG, "cars.1.monthly_cost", 0)
    document = changed(document, "cars.3.group", "E")
    write_yaml(files.catalog, document)

    error = failure(files)

    assert error.path == files.catalog
    assert error.problems == [
        "cars[1] (Car B).monthly_cost: Input should be greater than 0",
        "cars[3] (Car D).group: Input should be 'A', 'B', 'C' or 'D'",
    ]


def test_an_invalid_occupation_is_named_by_its_trimmed_name(files: Files) -> None:
    write_yaml(files.occupations, changed(OCCUPATIONS, "occupations.1.car_maintenance", -1))

    error = failure(files)

    assert error.path == files.occupations
    assert error.problems == [
        "occupations[1] (Field sales).car_maintenance: Input should be greater than or equal to 0"
    ]


def test_an_entry_without_a_usable_name_is_named_by_position_only(files: Files) -> None:
    document = changed(CATALOG, "cars.1.display_name", " ")
    document = changed(document, "cars.2", "not a car")
    write_yaml(files.catalog, document)

    assert failure(files).problems == [
        "cars[1].display_name: Value error, must not be blank",
        "cars[2]: Input should be a valid dictionary or instance of CatalogCar",
    ]


@pytest.mark.parametrize("text", ["", "# only a comment\n", "- a\n- b\n"])
def test_a_file_that_is_not_a_mapping_is_reported_without_a_location(
    files: Files, text: str
) -> None:
    files.catalog.write_text(text, encoding="utf-8")

    problems = failure(files).problems

    assert len(problems) == 1
    assert problems[0].startswith("Input should be a valid dictionary")


def test_a_file_that_is_not_utf8_is_reported(files: Files) -> None:
    files.occupations.write_bytes(b"occupations:\n  - name: \xe0\xe1\xe2\n")

    error = failure(files)

    assert error.path == files.occupations
    assert error.problems == ["cannot decode the file as UTF-8"]
