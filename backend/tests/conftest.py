from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import settings
from pydantic_ai import models
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Importing the model modules registers their tables on Base.metadata.
import car_advisor.conversations.models  # noqa: F401
import car_advisor.dataset.models  # noqa: F401
from car_advisor.dataset.importer import ImportResult, import_tax_year
from car_advisor.db import Base

REAL_DATASET = Path(__file__).resolve().parents[2] / "data" / "usage-values-2026.csv"

settings.register_profile(
    "default",
    max_examples=100,
    deadline=None,
    derandomize=True,
    database=None,
)
settings.load_profile("default")

# A test that reaches a real model fails instead of calling OpenAI. TestModel and FunctionModel
# are not affected.
models.ALLOW_MODEL_REQUESTS = False


def _memory_engine() -> Engine:
    # Every session shares the one connection that holds the in-memory database, and
    # that connection may be used from the threads that run the app under test.
    # The default transaction control starts a transaction only at the first INSERT, UPDATE
    # or DELETE, so a SAVEPOINT that comes first starts it and its RELEASE commits it. With
    # autocommit=False a transaction is always open, and savepoints nest as on SQL Server.
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False, "autocommit": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_engine() -> Iterator[Engine]:
    engine = _memory_engine()
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(db_engine)


# The real dataset gets its own engine for the whole run, so its 77,988 records are imported
# once and the per-test databases stay empty.
@pytest.fixture(scope="session")
def real_engine() -> Iterator[Engine]:
    engine = _memory_engine()
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def real_import(real_engine: Engine) -> ImportResult:
    return import_tax_year(sessionmaker(real_engine), REAL_DATASET, 2026)


@pytest.fixture(scope="session")
def real_sessions(real_engine: Engine, real_import: ImportResult) -> sessionmaker[Session]:
    # Requesting real_import guarantees that the records are in before a test reads them.
    return sessionmaker(real_engine)
