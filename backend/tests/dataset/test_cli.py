import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import sessionmaker

from car_advisor.dataset import cli
from car_advisor.dataset.models import UsageValueRow
from car_advisor.settings import Settings

# The parser skips the header row without reading it.
HEADER = "header"

PASSWORD = "Test-Only-Passw0rd"


def record(model_code: str, usage_value: int) -> str:
    return (
        f"2026,2020,1,01229,TESLA USA,{model_code},3E7E,4,MODEL 3,1,3,000,01,00000,"
        f"02305,06/01/2020,322200,377764,{usage_value}"
    )


def write_dataset(path: Path, *records: str) -> Path:
    path.write_bytes("".join(f"{line}\r\n" for line in (HEADER, *records)).encode("windows-1255"))
    return path


def stored(engine: Engine) -> set[tuple[str, int]]:
    query = select(UsageValueRow.model_code, UsageValueRow.usage_value)
    with sessionmaker(engine)() as session:
        return set(session.execute(query).all())


@pytest.fixture(autouse=True)
def settings_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Settings reads ../.env. From this working directory it finds no file, so a
    # developer's own .env cannot change the outcome of a test.
    workdir = tmp_path / "backend"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    for key in list(os.environ):
        if key.lower() in Settings.model_fields:
            monkeypatch.delenv(key)

    dataset = tmp_path / "settings.csv"
    env = {
        "DATASET_PATH": str(dataset),
        "TAX_YEAR": "2026",
        "DB_HOST": "localhost",
        "DB_NAME": "car_advisor",
        "DB_USER": "sa",
        "DB_PASSWORD": PASSWORD,
        "LLM_MODEL": "test-model",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return dataset


@pytest.fixture
def ensured_databases(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    names: list[str] = []
    monkeypatch.setattr(cli, "ensure_database", lambda settings: names.append(settings.db_name))
    return names


@pytest.fixture
def database(
    tmp_path: Path, ensured_databases: list[str], monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    # A file database, because the command disposes its engine and that would erase an
    # in-memory one. The migrations run for real, so the command builds the schema itself.
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setattr(cli, "make_engine", lambda settings: engine)
    yield engine
    engine.dispose()


def test_a_small_file_reloads_its_year_and_prints_the_count(
    tmp_path: Path,
    database: Engine,
    ensured_databases: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = write_dataset(tmp_path / "first.csv", record("00001", 7990), record("00002", 8100))
    reload = write_dataset(
        tmp_path / "reload.csv", record("00002", 8200), record("00003", 9000), record("00004", 9100)
    )

    assert cli.main(["--tax-year", "2026", "--path", str(first)]) == 0
    assert capsys.readouterr().out == "Tax year 2026 loaded, record count: 2\n"
    assert cli.main(["--tax-year", "2026", "--path", str(reload)]) == 0
    assert capsys.readouterr().out == "Tax year 2026 loaded, record count: 3\n"

    assert stored(database) == {("00002", 8200), ("00003", 9000), ("00004", 9100)}
    assert ensured_databases == ["car_advisor", "car_advisor"]


def test_the_path_defaults_to_the_app_settings_path(
    settings_dataset: Path, database: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    write_dataset(settings_dataset, record("00001", 7990), record("00002", 8100))

    assert cli.main(["--tax-year", "2026"]) == 0

    assert capsys.readouterr().out == "Tax year 2026 loaded, record count: 2\n"
    assert stored(database) == {("00001", 7990), ("00002", 8100)}


def test_a_malformed_file_exits_non_zero_with_the_line_and_the_reason(
    tmp_path: Path, database: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    good = write_dataset(tmp_path / "good.csv", record("00001", 7990))
    malformed = write_dataset(
        tmp_path / "malformed.csv", record("00002", 8100), "2026,2020,1", record("00003", 9000)
    )
    assert cli.main(["--tax-year", "2026", "--path", str(good)]) == 0
    capsys.readouterr()

    assert cli.main(["--tax-year", "2026", "--path", str(malformed)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{malformed}, line 3: expected 19 columns, found 3\n"
    assert stored(database) == {("00001", 7990)}


def test_a_missing_file_exits_non_zero_with_the_path_and_the_reason(
    tmp_path: Path, database: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.csv"

    assert cli.main(["--tax-year", "2026", "--path", str(missing)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{missing}: file not found\n"


def test_the_tax_year_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main([])

    assert caught.value.code == 2
    assert "--tax-year" in capsys.readouterr().err


def test_invalid_settings_exit_non_zero_without_echoing_any_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TAX_YEAR")

    assert cli.main(["--tax-year", "2026"]) == 1

    # Pydantic's own message for a missing field lists the other inputs, the password too.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "invalid settings: TAX_YEAR: missing\n"
