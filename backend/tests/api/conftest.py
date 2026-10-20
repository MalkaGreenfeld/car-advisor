from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine

import car_advisor.api.app as app_module
from car_advisor.db import run_migrations
from car_advisor.settings import Settings
from tests.dataset.test_import import record, write_dataset

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
PASSWORD = "Test-Only-Passw0rd"

CAR_MISSING = "2026-2020-1-01229-00099"

CATALOG = """
cars:
  - display_name: "Tesla Model 3"
    official_key:
      tax_year: 2026
      registration_year: 2020
      vehicle_type_code: "1"
      manufacturer_code: "01229"
      model_code: "{model_code}"
    monthly_cost: 4000
    group: C
"""


def make_settings(directory: Path, **overrides: object) -> Settings:
    """App_Settings on the files of `directory`, with the real rules and occupations."""
    values: dict[str, object] = {
        "dataset_path": directory / "usage-values.csv",
        "tax_year": 2026,
        "db_host": "db.invalid",
        "db_name": "car_advisor",
        "db_user": "sa",
        "db_password": PASSWORD,
        "llm_model": "test-model",
        "openai_api_key": None,
        "rules_path": CONFIG_DIR / "rules.yaml",
        "catalog_path": directory / "car_catalog.yaml",
        "occupations_path": CONFIG_DIR / "occupations.yaml",
    }
    return Settings(_env_file=None, **(values | overrides))


def write_catalog(directory: Path, model_code: str = "00001") -> None:
    (directory / "car_catalog.yaml").write_text(CATALOG.format(model_code=model_code))


@pytest.fixture
def app_settings(tmp_path: Path) -> Settings:
    """Settings whose dataset holds two cars and whose catalog names one of them."""
    write_dataset(
        tmp_path / "usage-values.csv", record(2026, "00001", 7990), record(2026, "00002", 8100)
    )
    write_catalog(tmp_path)
    return make_settings(tmp_path)


@pytest.fixture
def sqlite_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run the startup sequence on a SQLite file instead of SQL Server, with the real migrations.

    A file keeps its data from one start of an app to the next, as SQL Server does. The JSON
    handler of configure_logging stays off, so a test reads the log lines through caplog.
    Returns the path of the file.
    """
    database = tmp_path / "advisor.db"

    def open_sqlite(settings: Settings) -> Engine:
        engine = create_engine(f"sqlite+pysqlite:///{database}")
        run_migrations(engine)
        return engine

    monkeypatch.setattr(app_module, "open_database", open_sqlite)
    monkeypatch.setattr(app_module, "configure_logging", lambda: None)
    return database
