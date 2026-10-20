import os
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from car_advisor.settings import Settings, settings_error_fields

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

VALID_REQUIRED_ENV = {
    "DATASET_PATH": "../data/usage-values-2026.csv",
    "TAX_YEAR": "2026",
    "DB_HOST": "localhost",
    "DB_NAME": "car_advisor",
    "DB_USER": "sa",
    "DB_PASSWORD": "Test-Only-Passw0rd",
    "LLM_MODEL": "test-model",
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Variables match field names case-insensitively, so every spelling must go.
    for key in list(os.environ):
        if key.lower() in Settings.model_fields:
            monkeypatch.delenv(key)


def settings_error(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> ValidationError:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    return caught.value


def example_variables() -> dict[str, str]:
    """Map each variable in `.env.example` to the comment line above it."""
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    return {
        line.split("=", 1)[0]: previous
        for previous, line in pairwise(lines)
        if line and not line.startswith("#")
    }


def test_env_example_parses_without_api_key() -> None:
    settings = Settings(_env_file=ENV_EXAMPLE)

    assert settings.openai_api_key is None
    assert settings.tax_year == 2026
    assert settings.dataset_path == Path("../data/usage-values-2026.csv")


def test_env_example_marks_every_variable_but_the_api_key_required() -> None:
    variables = example_variables()

    assert set(variables) == {name.upper() for name in Settings.model_fields}
    not_required = {name for name, note in variables.items() if not note.startswith("# Required.")}
    assert not_required == {"OPENAI_API_KEY"}
    assert variables["OPENAI_API_KEY"].startswith("# Optional.")


@pytest.mark.parametrize("variable", sorted(VALID_REQUIRED_ENV))
def test_missing_required_variable_is_named(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    env = {key: value for key, value in VALID_REQUIRED_ENV.items() if key != variable}

    assert settings_error_fields(settings_error(monkeypatch, env)) == [f"{variable}: missing"]


@pytest.mark.parametrize("variable", sorted(VALID_REQUIRED_ENV))
def test_empty_required_variable_is_named(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    env = {**VALID_REQUIRED_ENV, variable: ""}

    assert settings_error_fields(settings_error(monkeypatch, env)) == [f"{variable}: missing"]


@pytest.mark.parametrize("db_name", ["car-advisor", "1st_db", "db]; DROP DATABASE master; --"])
def test_invalid_db_name_is_named(monkeypatch: pytest.MonkeyPatch, db_name: str) -> None:
    error = settings_error(monkeypatch, {**VALID_REQUIRED_ENV, "DB_NAME": db_name})

    assert settings_error_fields(error) == ["DB_NAME: string_pattern_mismatch"]


def test_error_fields_leave_out_input_values(monkeypatch: pytest.MonkeyPatch) -> None:
    invalid = {"TAX_YEAR": "year-2026x", "DB_PORT": "port-1433x", "DB_NAME": "bad]name"}

    fields = settings_error_fields(settings_error(monkeypatch, {**VALID_REQUIRED_ENV, **invalid}))

    assert fields == [
        "TAX_YEAR: int_parsing",
        "DB_PORT: int_parsing",
        "DB_NAME: string_pattern_mismatch",
    ]
    text = "\n".join(fields)
    for value in [*invalid.values(), VALID_REQUIRED_ENV["DB_PASSWORD"]]:
        assert value not in text
