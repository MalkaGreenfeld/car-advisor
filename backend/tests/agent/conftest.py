from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel as StubModel
from pydantic_ai.usage import RunUsage
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.agent.tools import AdvisorDeps
from car_advisor.conversations.service import ConversationService
from car_advisor.dataset.lookup import CarLookup
from car_advisor.rules.loader import AppConfig, load_config

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    return load_config(
        CONFIG_DIR / "rules.yaml",
        CONFIG_DIR / "car_catalog.yaml",
        CONFIG_DIR / "occupations.yaml",
    )


@pytest.fixture
def lookup(real_sessions: sessionmaker[Session]) -> CarLookup:
    return CarLookup(real_sessions, 2026)


# The conversations live in the per-test database and the cars in the shared one with the real
# dataset. They are two engines on purpose: every session of one in-memory SQLite engine shares
# its single connection, so a lookup that closes its session would roll back the open
# transaction of the service.
@pytest.fixture
def service(
    sessions: sessionmaker[Session], lookup: CarLookup, app_config: AppConfig
) -> ConversationService:
    return ConversationService(sessions, lookup, app_config)


@pytest.fixture
def deps(service: ConversationService, lookup: CarLookup, app_config: AppConfig) -> AdvisorDeps:
    return AdvisorDeps(service.create(), service, lookup, app_config)


@pytest.fixture
def ctx(deps: AdvisorDeps) -> RunContext[AdvisorDeps]:
    return RunContext(deps=deps, model=StubModel(), usage=RunUsage())
