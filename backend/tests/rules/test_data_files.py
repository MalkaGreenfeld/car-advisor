import re
from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.dataset.lookup import CarLookup
from car_advisor.engine.config import CarCatalog, OccupationsTable
from car_advisor.engine.types import CarKey, CarRecord, Group, Powertrain
from car_advisor.rules.loader import AppConfig, load_config

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
RULES_FILE = CONFIG_DIR / "rules.yaml"
CATALOG_FILE = CONFIG_DIR / "car_catalog.yaml"
OCCUPATIONS_FILE = CONFIG_DIR / "occupations.yaml"

HEBREW_LETTER = re.compile(r"[\u05d0-\u05ea]")


@pytest.fixture(scope="module")
def config() -> AppConfig:
    return load_config(RULES_FILE, CATALOG_FILE, OCCUPATIONS_FILE)


@pytest.fixture(scope="module")
def lookup(real_sessions: sessionmaker[Session]) -> CarLookup:
    return CarLookup(real_sessions, 2026)


@pytest.fixture(scope="module")
def records(config: AppConfig, lookup: CarLookup) -> dict[CarKey, CarRecord]:
    return lookup.get_many(car.official_key for car in config.catalog.cars)


def test_the_data_files_load_through_the_loader_with_the_tracked_rules(config: AppConfig) -> None:
    assert isinstance(config.catalog, CarCatalog)
    assert isinstance(config.occupations, OccupationsTable)


@pytest.mark.parametrize("path", [CATALOG_FILE, OCCUPATIONS_FILE], ids=lambda path: path.name)
def test_each_file_carries_the_mock_data_marker(path: Path) -> None:
    marker = yaml.safe_load(path.read_text(encoding="utf-8"))["mock_data"]

    assert "MOCK DATA" in marker
    assert "replace" in marker
    assert "2026-10-12" in marker


def test_the_catalog_has_the_20_cars_of_the_challenge(config: AppConfig) -> None:
    cars = config.catalog.cars
    regular = [car for car in cars if not car.family_track]

    assert len(cars) == 20
    assert len(regular) == 18
    assert {car.group for car in regular} == set(Group)


def test_every_catalog_key_is_in_the_dataset(config: AppConfig, lookup: CarLookup) -> None:
    keys = [car.official_key for car in config.catalog.cars]

    assert lookup.missing_keys(keys) == []


def test_every_car_has_the_challenges_vehicle_type_and_a_registration_year_of_2025_or_2026(
    config: AppConfig,
) -> None:
    keys = [car.official_key for car in config.catalog.cars]

    assert {key.vehicle_type_code for key in keys} == {"1"}
    assert {key.registration_year for key in keys} == {2025, 2026}


def test_the_costs_and_usage_values_are_those_of_the_challenge_sheet(
    config: AppConfig, records: dict[CarKey, CarRecord]
) -> None:
    picanto = next(car for car in config.catalog.cars if car.display_name == "Kia Picanto")
    carnival = next(car for car in config.catalog.cars if car.display_name == "Kia Carnival")

    assert (picanto.monthly_cost, picanto.default_participation) == (2650, 150)
    assert records[picanto.official_key].usage_value == 2690
    assert (carnival.monthly_cost, carnival.default_participation) == (6000, 500)
    assert records[carnival.official_key].usage_value == 8120


def test_at_least_three_cars_are_electric_by_their_dataset_records(
    config: AppConfig, records: dict[CarKey, CarRecord]
) -> None:
    electric = [
        car.display_name
        for car in config.catalog.cars
        if records[car.official_key].powertrain is Powertrain.ELECTRIC
    ]

    assert len(electric) >= 3, electric


def test_two_cars_are_family_track_cars_and_both_are_in_group_a(config: AppConfig) -> None:
    family_track = {car.display_name: car for car in config.catalog.cars if car.family_track}

    assert {name: car.monthly_cost for name, car in family_track.items()} == {
        "Kia Carnival": 6000,
        "Hyundai Santa Fe": 5600,
    }
    assert {car.group for car in family_track.values()} == {Group.A}


def test_every_car_has_a_default_participation(config: AppConfig) -> None:
    assert all(car.default_participation is not None for car in config.catalog.cars)


def test_every_group_has_a_car_that_costs_less_than_its_budget(config: AppConfig) -> None:
    budgets = config.rules.groups.budgets
    groups_with_unused_budget = {
        car.group
        for car in config.catalog.cars
        if not car.family_track and car.monthly_cost < budgets[car.group]
    }

    assert groups_with_unused_budget == set(Group)


def test_the_occupations_are_the_14_of_the_challenge(config: AppConfig) -> None:
    occupations = config.occupations.occupations

    assert len(occupations) == 14
    assert sum(not item.car_waiver_allowed for item in occupations) == 4


def test_giving_up_the_car_is_allowed_only_with_a_car_maintenance_amount(
    config: AppConfig,
) -> None:
    for item in config.occupations.occupations:
        assert (item.car_maintenance > 0) is item.car_waiver_allowed, item.name


def test_occupation_names_are_hebrew(config: AppConfig) -> None:
    assert all(HEBREW_LETTER.search(item.name) for item in config.occupations.occupations)


@pytest.mark.parametrize(
    ("typed", "waiver_allowed", "maintenance"),
    [
        pytest.param("מפתחת תוכנה", True, 1500, id="feminine"),
        pytest.param("מפתח תוכנה", True, 1500, id="masculine"),
        pytest.param("אנליסטית נתונים", True, 1500, id="analyst-feminine"),
        pytest.param("נציגת מכירות שטח", False, 0, id="field-sales-feminine"),
        pytest.param("מנהל חטיבה", True, 3000, id="division-manager"),
    ],
)
def test_an_occupation_typed_in_either_gender_finds_its_entry(
    config: AppConfig, typed: str, waiver_allowed: bool, maintenance: int
) -> None:
    entry = config.occupations.find(typed)

    assert entry is not None
    assert (entry.car_waiver_allowed, entry.car_maintenance) == (waiver_allowed, maintenance)
