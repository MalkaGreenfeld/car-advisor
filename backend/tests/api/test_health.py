import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from inspect import iscoroutinefunction
from pathlib import Path
from typing import Any, NoReturn

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from car_advisor.api.app import create_app
from car_advisor.api.health import router as health_router
from car_advisor.dataset.importer import import_tax_year
from car_advisor.dataset.models import UsageValueRow
from car_advisor.logs import JsonLinesFormatter
from car_advisor.settings import Settings
from tests.dataset.test_import import record, write_dataset

HEALTH = "/api/health"
DATABASE_DOWN = {
    "database_reachable": False,
    "dataset_loaded": False,
    "tax_year": 2026,
    "record_count": 0,
}


@pytest.fixture
def app(app_settings: Settings, sqlite_startup: Path) -> FastAPI:
    return create_app(app_settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as started:
        yield started


def stub_database(app: FastAPI, connect: Callable[[], NoReturn]) -> None:
    """Point the started app at a database whose every connection attempt runs `connect`."""
    engine = create_engine("sqlite+pysqlite://", creator=connect, poolclass=NullPool)
    app.state.services = replace(app.state.services, sessions=sessionmaker(engine))


def logged(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """The lines written so far, as the stdout formatter renders them."""
    formatter = JsonLinesFormatter()
    return [json.loads(formatter.format(line)) for line in caplog.records]


def failure(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    [line] = [line for line in logged(caplog) if line["event"] == "health_check_failed"]
    return line


def test_a_loaded_dataset_gives_200_with_the_record_count_and_needs_no_api_key(
    app: FastAPI, client: TestClient
) -> None:
    assert app.state.services.agent_available is False

    response = client.get(HEALTH)

    assert (response.status_code, response.json()) == (
        200,
        {"database_reachable": True, "dataset_loaded": True, "tax_year": 2026, "record_count": 2},
    )


def test_only_the_records_of_the_configured_tax_year_are_counted(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    other_year = write_dataset(tmp_path / "2025.csv", record(2025, "00001", 7000))
    import_tax_year(app.state.services.sessions, other_year, 2025)

    assert client.get(HEALTH).json()["record_count"] == 2


def test_a_reachable_database_without_the_dataset_gives_503(
    app: FastAPI, client: TestClient
) -> None:
    with app.state.services.sessions.begin() as session:
        session.execute(delete(UsageValueRow))

    response = client.get(HEALTH)

    assert (response.status_code, response.json()) == (
        503,
        {"database_reachable": True, "dataset_loaded": False, "tax_year": 2026, "record_count": 0},
    )


def test_a_refused_connection_gives_503_and_logs_the_error_type_and_never_its_message(
    app: FastAPI, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse() -> NoReturn:
        raise sqlite3.OperationalError("Server=db.invalid;Password=hunter2")

    stub_database(app, refuse)

    started = time.perf_counter()
    response = client.get(HEALTH)
    elapsed = time.perf_counter() - started

    assert elapsed < 5
    assert (response.status_code, response.json()) == (503, DATABASE_DOWN)
    assert failure(caplog)["error_type"] == "OperationalError"
    assert "hunter2" not in json.dumps(logged(caplog))


def test_a_database_that_never_answers_gives_503_within_5_seconds(
    app: FastAPI, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    release = threading.Event()

    def never_answer() -> NoReturn:
        release.wait()
        raise sqlite3.OperationalError("released after the request")

    stub_database(app, never_answer)

    started = time.perf_counter()
    try:
        response = client.get(HEALTH)
    finally:
        # The worker thread of the check cannot be stopped, and the app waits for it at shutdown.
        release.set()
    elapsed = time.perf_counter() - started

    assert elapsed < 5
    assert (response.status_code, response.json()) == (503, DATABASE_DOWN)
    assert failure(caplog)["error_type"] == "TimeoutError"


def test_the_health_route_resolves_its_dependencies_without_the_thread_pool() -> None:
    # A plain def dependency runs in the thread pool of the sync routes, which calls to a database
    # that hangs can fill, and the route would wait for a free thread (Req 14.9).
    [route] = [r for r in health_router.routes if isinstance(r, APIRoute)]

    assert route.dependant.dependencies
    assert all(iscoroutinefunction(d.call) for d in route.dependant.dependencies)
