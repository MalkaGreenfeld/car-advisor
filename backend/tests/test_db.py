from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, event, inspect

from car_advisor.db import Base, database_url, enable_read_committed_snapshot, run_migrations
from car_advisor.settings import Settings

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
PASSWORD = "Test-Only-Passw0rd;{x}@:/"


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        dataset_path=Path("../data/usage-values-2026.csv"),
        tax_year=2026,
        db_host="db.internal",
        db_port=14330,
        db_name="car_advisor",
        db_user="sa",
        db_password=PASSWORD,
        llm_model="test-model",
    )


@pytest.fixture
def migrated(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'migrated.db'}")
    run_migrations(engine)
    yield engine
    engine.dispose()


def test_database_url_uses_mssql_python_with_tls_options() -> None:
    url = database_url(make_settings())

    assert url.drivername == "mssql+mssqlpython"
    assert (url.host, url.port, url.database, url.username) == (
        "db.internal",
        14330,
        "car_advisor",
        "sa",
    )
    assert dict(url.query) == {"Encrypt": "yes", "TrustServerCertificate": "yes"}


def test_database_url_can_target_another_database() -> None:
    assert database_url(make_settings(), "master").database == "master"


def test_database_url_never_shows_the_password() -> None:
    url = database_url(make_settings())

    assert url.password == PASSWORD
    assert PASSWORD not in str(url)
    assert PASSWORD not in repr(url)


def test_read_committed_snapshot_is_left_alone_when_the_database_is_not_on_sql_server(
    migrated: Engine,
) -> None:
    statements: list[str] = []
    event.listen(
        migrated,
        "before_cursor_execute",
        lambda connection, cursor, statement, *rest: statements.append(statement),
    )

    with migrated.connect() as connection:
        enable_read_committed_snapshot(connection, "car_advisor")

    assert statements == []


def test_the_migrated_schema_matches_the_models(migrated: Engine) -> None:
    with migrated.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []

    # compare_metadata ignores index filters, so the WHERE clauses are compared directly.
    inspector = inspect(migrated)
    migrated_filters = {
        index["name"]: str(index["dialect_options"]["sqlite_where"])
        for table in inspector.get_table_names()
        for index in inspector.get_indexes(table)
        if "sqlite_where" in index["dialect_options"]
    }
    model_filters = {
        index.name: str(index.dialect_options["sqlite"]["where"])
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.dialect_options["sqlite"]["where"] is not None
    }
    assert migrated_filters == model_filters


def test_running_the_migrations_again_changes_nothing(migrated: Engine) -> None:
    run_migrations(migrated)

    with migrated.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == "0002"
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_the_downgrade_drops_every_table(migrated: Engine) -> None:
    config = Config(ALEMBIC_INI)
    with migrated.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")

    assert inspect(migrated).get_table_names() == ["alembic_version"]
