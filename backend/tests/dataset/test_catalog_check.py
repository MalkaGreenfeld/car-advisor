from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.dataset.lookup import CarLookup, CatalogMismatch, check_catalog
from car_advisor.engine.config import CarCatalog, CatalogCar
from car_advisor.engine.types import CarKey, Group
from car_advisor.rules.loader import load_config

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def catalog_car(name: str, key: str) -> CatalogCar:
    return CatalogCar(
        display_name=name,
        official_key=CarKey.parse(key),
        monthly_cost=Decimal(3000),
        group=Group.A,
    )


# Each unknown key differs from the Model 3 key in one part, so only an exact match finds it.
MODEL_3 = catalog_car("Tesla Model 3", "2026-2023-1-01229-00177")
UNKNOWN_MODEL_CODE = catalog_car("Ghost Model Code", "2026-2023-1-01229-00178")
UNKNOWN_YEAR = catalog_car("Ghost Registration Year", "2026-2024-1-01229-00177")


@pytest.fixture(scope="module")
def lookup(real_sessions: sessionmaker[Session]) -> CarLookup:
    return CarLookup(real_sessions, 2026)


def test_the_mock_catalog_passes_against_the_real_dataset(lookup: CarLookup) -> None:
    config = load_config(
        CONFIG_DIR / "rules.yaml",
        CONFIG_DIR / "car_catalog.yaml",
        CONFIG_DIR / "occupations.yaml",
    )

    assert check_catalog(lookup, config.catalog) is None


def test_a_catalog_with_two_unknown_keys_names_both_cars(lookup: CarLookup) -> None:
    catalog = CarCatalog(cars=[UNKNOWN_MODEL_CODE, MODEL_3, UNKNOWN_YEAR])

    with pytest.raises(CatalogMismatch) as caught:
        check_catalog(lookup, catalog)

    assert caught.value.missing == [UNKNOWN_MODEL_CODE, UNKNOWN_YEAR]
    assert str(caught.value) == (
        "Car_Catalog cars not found in the Usage_Value_Dataset by Official_Key:\n"
        "  Ghost Model Code: 2026-2023-1-01229-00178\n"
        "  Ghost Registration Year: 2026-2024-1-01229-00177"
    )
