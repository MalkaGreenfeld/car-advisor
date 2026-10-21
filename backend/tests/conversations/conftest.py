from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.conversations.service import ConversationService
from car_advisor.dataset.lookup import CarLookup
from car_advisor.engine.types import CarKey
from car_advisor.rules.loader import AppConfig, load_config

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    return load_config(
        CONFIG_DIR / "rules.yaml",
        CONFIG_DIR / "car_catalog.yaml",
        CONFIG_DIR / "occupations.yaml",
    )


def without_default_participation(config: AppConfig, *keys: CarKey) -> AppConfig:
    """The config without a Default_Participation for the given cars, which then wait for one."""
    cars = [
        car.model_copy(update={"default_participation": None}) if car.official_key in keys else car
        for car in config.catalog.cars
    ]
    return replace(config, catalog=config.catalog.model_copy(update={"cars": cars}))


# The service works on the per-test SQLite database, which holds no dataset records.
@pytest.fixture
def service(sessions: sessionmaker[Session], app_config: AppConfig) -> ConversationService:
    return ConversationService(sessions, CarLookup(sessions, 2026), app_config)


# The conversations live in the per-test database and the cars in the shared one with the real
# dataset. They are two engines on purpose: every session of one in-memory SQLite engine shares
# its single connection, so a lookup that closes its session would roll back the open
# transaction of the service.
@pytest.fixture
def dataset_service(
    sessions: sessionmaker[Session], real_sessions: sessionmaker[Session], app_config: AppConfig
) -> ConversationService:
    return ConversationService(sessions, CarLookup(real_sessions, 2026), app_config)
