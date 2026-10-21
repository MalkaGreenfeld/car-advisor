import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.test import TestModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError

import car_advisor.api.app as app_module
from car_advisor.api.app import SettingsError, build_model, create_app
from car_advisor.api.services import Services, get_services
from car_advisor.conversations.service import MessageRole
from car_advisor.dataset.importer import DatasetImportError
from car_advisor.dataset.lookup import CatalogMismatch
from car_advisor.dataset.models import UsageValueRow
from car_advisor.logs import JsonLinesFormatter
from car_advisor.rules.loader import ConfigError
from car_advisor.settings import Settings
from tests.api.conftest import CAR_MISSING, PASSWORD, write_catalog

LOGGER = "car_advisor.api.app"
API_KEY = "sk-test-Only-key-0000"


@pytest.fixture(autouse=True)
def startup_lines(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)


def logged(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """The lines of the app written so far, as the stdout formatter renders them."""
    formatter = JsonLinesFormatter()
    return [
        json.loads(formatter.format(record)) for record in caplog.records if record.name == LOGGER
    ]


def events(caplog: pytest.LogCaptureFixture, prefix: str) -> list[str]:
    return [line["event"] for line in logged(caplog) if line["event"].startswith(prefix)]


def failure(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    [line] = [line for line in logged(caplog) if line["event"] == "startup_failed"]
    return line


def start(settings: Settings) -> Services:
    """Run the startup sequence and the shutdown, and give the services that it built."""
    app = create_app(settings)
    with TestClient(app):
        services: Services = app.state.services
    return services


def stored_records(database: Path) -> int:
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    with engine.connect() as connection:
        count = connection.execute(select(func.count()).select_from(UsageValueRow)).scalar_one()
    engine.dispose()
    return count


def test_a_config_error_stops_startup_and_logs_the_path_and_every_problem(
    app_settings: Settings, sqlite_startup: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rules = tmp_path / "broken-rules.yaml"
    rules.write_text("groups: 5\n")

    with pytest.raises(ConfigError):
        start(app_settings.model_copy(update={"rules_path": rules}))

    line = failure(caplog)
    assert (line["stage"], line["error_type"]) == ("config", "ConfigError")
    path, *problems = line["fields"]
    assert path == str(rules)
    assert any(problem.startswith("groups:") for problem in problems)
    assert len(problems) > 1


def test_the_dataset_is_imported_only_when_the_year_has_no_records(
    app_settings: Settings, sqlite_startup: Path, caplog: pytest.LogCaptureFixture
) -> None:
    start(app_settings)

    [imported] = [line for line in logged(caplog) if line["event"] == "dataset_imported"]
    assert (imported["tax_year"], imported["record_count"]) == (2026, 2)
    assert stored_records(sqlite_startup) == 2

    # A second start that tried to read the file again would fail.
    app_settings.dataset_path.unlink()
    caplog.clear()
    start(app_settings)

    assert events(caplog, "dataset_") == ["dataset_present"]
    assert stored_records(sqlite_startup) == 2


def test_a_dataset_error_stops_startup_and_logs_the_file_and_the_reason(
    app_settings: Settings, sqlite_startup: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app_settings.dataset_path.unlink()

    with pytest.raises(DatasetImportError):
        start(app_settings)

    line = failure(caplog)
    assert (line["stage"], line["error_type"]) == ("import", "DatasetImportError")
    assert line["fields"] == [f"{app_settings.dataset_path}: file not found"]


def test_a_catalog_car_missing_from_the_dataset_stops_startup_and_is_listed(
    app_settings: Settings, sqlite_startup: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write_catalog(tmp_path, "00099")

    with pytest.raises(CatalogMismatch):
        start(app_settings)

    line = failure(caplog)
    assert (line["stage"], line["error_type"]) == ("catalog_check", "CatalogMismatch")
    assert line["fields"] == [f"Tesla Model 3: {CAR_MISSING}"]


def test_a_differing_catalog_usage_value_starts_the_app_and_logs_one_warning_per_car(
    app_settings: Settings, sqlite_startup: Path, caplog: pytest.LogCaptureFixture
) -> None:
    catalog = app_settings.catalog_path
    catalog.write_text(catalog.read_text() + "    usage_value: 7000\n")

    services = start(app_settings)

    [warning] = services.catalog_warnings
    assert (warning.catalog_usage_value, warning.dataset_usage_value) == (7000, 7990)
    lines = [line for line in logged(caplog) if line["event"] == "catalog_usage_value_mismatch"]
    assert [(line["level"], line["stage"], line["car_key"]) for line in lines] == [
        ("WARNING", "catalog_check", "2026-2020-1-01229-00001")
    ]
    assert events(caplog, "startup_failed") == []


def test_a_matching_catalog_usage_value_gives_no_warning(
    app_settings: Settings, sqlite_startup: Path, caplog: pytest.LogCaptureFixture
) -> None:
    catalog = app_settings.catalog_path
    catalog.write_text(catalog.read_text() + "    usage_value: 7990\n")

    assert start(app_settings).catalog_warnings == ()
    assert events(caplog, "catalog_usage_value_mismatch") == []


def test_without_an_api_key_the_agent_is_unavailable_and_a_warning_is_logged(
    app_settings: Settings, sqlite_startup: Path, caplog: pytest.LogCaptureFixture
) -> None:
    services = start(app_settings)

    assert services.agent_available is False
    [line] = [line for line in logged(caplog) if line["event"] == "agent_disabled"]
    assert (line["level"], line["stage"]) == ("WARNING", "agent")


def test_with_an_api_key_the_agent_is_available_and_the_key_is_not_logged(
    app_settings: Settings, sqlite_startup: Path, caplog: pytest.LogCaptureFixture
) -> None:
    services = start(app_settings.model_copy(update={"openai_api_key": SecretStr(API_KEY)}))

    assert services.agent_available is True
    assert "agent_disabled" not in events(caplog, "agent_")
    assert API_KEY not in json.dumps(logged(caplog))


def test_the_model_gets_the_key_of_the_settings_and_not_one_from_the_environment(
    app_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")

    model = build_model(app_settings, SecretStr(API_KEY))

    assert isinstance(model, OpenAIResponsesModel)
    assert model.model_name == "test-model"
    assert model.client.api_key == API_KEY


def test_a_test_model_can_stand_in_for_the_provider_in_the_started_app(
    monkeypatch: pytest.MonkeyPatch, app_settings: Settings, sqlite_startup: Path
) -> None:
    reply = TestModel(call_tools=[], custom_output_text="Hello")
    monkeypatch.setattr(app_module, "build_model", lambda settings, api_key: reply)
    app = create_app(app_settings.model_copy(update={"openai_api_key": SecretStr(API_KEY)}))

    with TestClient(app):
        services: Services = app.state.services
        conversation_id = services.conversations.create()
        asyncio.run(services.runner.run_turn(conversation_id, "Hi"))
        state = services.conversations.state(conversation_id)

    assert [(message.role, message.text) for message in state.messages] == [
        (MessageRole.EMPLOYEE, "Hi"),
        (MessageRole.ADVISOR, "Hello"),
    ]


def test_a_provider_other_than_openai_stops_startup_when_a_key_is_set(
    app_settings: Settings, sqlite_startup: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = app_settings.model_copy(
        update={"openai_api_key": SecretStr(API_KEY), "llm_provider": "anthropic"}
    )

    with pytest.raises(SettingsError):
        start(settings)

    line = failure(caplog)
    assert (line["stage"], line["fields"]) == ("agent", ["LLM_PROVIDER: unsupported_provider"])


def clear_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.lower() in Settings.model_fields:
            monkeypatch.delenv(key)


def test_a_settings_error_logs_the_variable_names_and_no_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Settings reads ../.env, which a developer's own file could fill in.
    monkeypatch.chdir(tmp_path)
    clear_settings_environment(monkeypatch)
    monkeypatch.setattr(app_module, "configure_logging", lambda: None)
    environment = {
        "DATASET_PATH": "data.csv",
        "TAX_YEAR": "not-a-year",
        "DB_HOST": "db.invalid",
        "DB_NAME": "car_advisor",
        "DB_USER": "sa",
        "DB_PASSWORD": PASSWORD,
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(SettingsError) as raised, TestClient(create_app()):
        pass

    line = failure(caplog)
    assert (line["stage"], line["error_type"]) == ("settings", "SettingsError")
    assert line["fields"] == ["TAX_YEAR: int_parsing", "LLM_MODEL: missing"]
    # uvicorn prints the exception that stops the startup, so it must carry no value either.
    printed = json.dumps(logged(caplog)) + str(raised.value)
    assert PASSWORD not in printed
    assert "not-a-year" not in printed
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


@pytest.mark.parametrize(
    ("step", "stage"), [("ensure_database", "create_database"), ("run_migrations", "migrate")]
)
def test_a_database_failure_logs_its_stage_and_the_error_type_and_no_message(
    monkeypatch: pytest.MonkeyPatch,
    app_settings: Settings,
    caplog: pytest.LogCaptureFixture,
    step: str,
    stage: str,
) -> None:
    secret = "Server=db.invalid;Password=hunter2"

    def refuse(*arguments: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception(secret))

    monkeypatch.setattr(app_module, "configure_logging", lambda: None)
    monkeypatch.setattr(app_module, "ensure_database", lambda settings: None)
    monkeypatch.setattr(app_module, "make_engine", lambda s: create_engine("sqlite+pysqlite://"))
    monkeypatch.setattr(app_module, "run_migrations", lambda engine: None)
    monkeypatch.setattr(app_module, step, refuse)

    with pytest.raises(OperationalError):
        start(app_settings)

    line = failure(caplog)
    assert (line["stage"], line["error_type"]) == (stage, "OperationalError")
    assert "fields" not in line
    assert "hunter2" not in json.dumps(logged(caplog))


def test_a_route_reaches_the_services_through_get_services(
    app_settings: Settings, sqlite_startup: Path
) -> None:
    app = create_app(app_settings)

    @app.get("/probe")
    def probe(services: Annotated[Services, Depends(get_services)]) -> dict[str, object]:
        return {"tax_year": services.lookup.tax_year, "agent_available": services.agent_available}

    with TestClient(app) as client:
        assert client.get("/probe").json() == {"tax_year": 2026, "agent_available": False}


def test_building_the_app_reads_neither_the_environment_nor_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    clear_settings_environment(monkeypatch)

    assert isinstance(create_app(), FastAPI)
    assert isinstance(app_module.app, FastAPI)
