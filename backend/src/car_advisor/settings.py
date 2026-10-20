from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmpty = Annotated[str, Field(min_length=1)]


class Settings(BaseSettings):
    # An empty variable counts as unset: an empty API key becomes None and an empty
    # required variable fails, so a copied `.env.example` starts without the agent.
    model_config = SettingsConfigDict(env_file="../.env", env_ignore_empty=True, extra="ignore")

    dataset_path: Path
    tax_year: int
    db_host: NonEmpty
    db_port: int = 1433
    db_name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    db_user: NonEmpty
    db_password: SecretStr
    llm_provider: NonEmpty = "openai"
    llm_model: NonEmpty
    openai_api_key: SecretStr | None = None
    rules_path: Path = Path("../config/rules.yaml")
    catalog_path: Path = Path("../config/car_catalog.yaml")
    occupations_path: Path = Path("../config/occupations.yaml")


def settings_error_fields(error: ValidationError) -> list[str]:
    """Describe each failure as `VARIABLE: error_type`.

    The input value is left out on purpose: it may be a secret such as the password.
    """
    return [f"{str(detail['loc'][0]).upper()}: {detail['type']}" for detail in error.errors()]
