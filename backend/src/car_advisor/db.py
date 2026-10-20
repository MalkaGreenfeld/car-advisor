from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Connection, DateTime, Engine, create_engine, text
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from car_advisor.settings import Settings

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


class Base(DeclarativeBase):
    # The generic DateTime renders as DATETIME on SQL Server; the schema uses DATETIME2.
    type_annotation_map = {datetime: DateTime().with_variant(mssql.DATETIME2(), "mssql")}


def utc_now() -> datetime:
    # The columns have no time zone, so UTC is stored without tzinfo.
    return datetime.now(UTC).replace(tzinfo=None)


def as_utc(stored: datetime) -> datetime:
    # An aware UTC time reaches the browser with a Z, which it cannot mistake for local time.
    return stored.replace(tzinfo=UTC)


def database_url(settings: Settings, database: str | None = None) -> URL:
    return URL.create(
        "mssql+mssqlpython",
        username=settings.db_user,
        password=settings.db_password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name if database is None else database,
        # The db container serves a self-signed certificate.
        query={"Encrypt": "yes", "TrustServerCertificate": "yes"},
    )


def make_engine(settings: Settings) -> Engine:
    return create_engine(database_url(settings))


def ensure_database(settings: Settings) -> None:
    # CREATE DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    master = create_engine(
        database_url(settings, "master"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    with master.connect() as connection:
        # Identifiers cannot be bound as parameters, so the name ends up inside the
        # CREATE DATABASE text. That is safe only because the DB_NAME pattern in Settings
        # allows identifier characters alone, so the name can never close the brackets.
        connection.execute(
            text("IF DB_ID(:name) IS NULL EXEC('CREATE DATABASE [' + :name + ']')"),
            {"name": settings.db_name},
        )
        enable_read_committed_snapshot(connection, settings.db_name)


def enable_read_committed_snapshot(connection: Connection, database: str) -> None:
    """Turn READ_COMMITTED_SNAPSHOT on for a SQL Server database that has it off.

    Under the default isolation, answers to two different questions of one conversation, or an
    answer racing a new rep question, deadlock on the conversation row. With row versioning a
    reader no longer waits for a writer, and the same requests all succeed. The connection must
    be in AUTOCOMMIT, as ALTER DATABASE cannot run inside a transaction. A database that is not
    on SQL Server is left alone.
    """
    if connection.dialect.name != "mssql":
        return
    is_on = connection.execute(
        text("SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = :name"),
        {"name": database},
    ).scalar_one()
    if not is_on:
        quoted = connection.dialect.identifier_preparer.quote_identifier(database)
        connection.exec_driver_sql(
            f"ALTER DATABASE {quoted} SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE"
        )


def run_migrations(engine: Engine) -> None:
    config = Config(_ALEMBIC_INI)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine)
