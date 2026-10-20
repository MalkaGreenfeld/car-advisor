from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import yaml
from pydantic import BaseModel, ValidationError

from car_advisor.engine.config import CarCatalog, OccupationsTable, RulesConfig


@dataclass(frozen=True)
class AppConfig:
    rules: RulesConfig
    catalog: CarCatalog
    occupations: OccupationsTable


class ConfigError(Exception):
    def __init__(self, path: Path, problems: list[str]) -> None:
        lines = "".join(f"\n  {problem}" for problem in problems)
        super().__init__(f"{path}:{lines}")
        self.path = path
        self.problems = problems


class _Entries(NamedTuple):
    list_field: str
    name_field: str


def load_config(rules_path: Path, catalog_path: Path, occupations_path: Path) -> AppConfig:
    """Raise a ConfigError that lists all problems of the first file that has any."""
    return AppConfig(
        rules=_load(rules_path, RulesConfig),
        catalog=_load(catalog_path, CarCatalog, _Entries("cars", "display_name")),
        occupations=_load(occupations_path, OccupationsTable, _Entries("occupations", "name")),
    )


def _load[T: BaseModel](path: Path, model: type[T], entries: _Entries | None = None) -> T:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(path, [f"file not found: {path}"]) from None
    except UnicodeDecodeError:
        raise ConfigError(path, ["cannot decode the file as UTF-8"]) from None

    try:
        document = yaml.safe_load(text)
    except yaml.MarkedYAMLError as exc:
        raise ConfigError(path, [f"line {exc.problem_mark.line + 1}: {exc.problem}"]) from None
    except yaml.reader.ReaderError as exc:
        # Not a MarkedYAMLError: it reports a character offset instead of a mark.
        line = text.count("\n", 0, exc.position) + 1
        raise ConfigError(path, [f"line {line}: {exc.reason}"]) from None

    try:
        return model.model_validate(document)
    except ValidationError as exc:
        raise ConfigError(path, _problems(exc, _entry_labels(document, entries))) from None


def _problems(error: ValidationError, labels: Mapping[str, str]) -> list[str]:
    problems: list[str] = []
    for detail in error.errors():
        place = _place(detail["loc"], labels)
        problems.append(f"{place}: {detail['msg']}" if place else detail["msg"])
    return problems


def _place(loc: tuple[int | str, ...], labels: Mapping[str, str]) -> str:
    place = ""
    for part in loc:
        if isinstance(part, int):
            place += f"[{part}]"
        else:
            place += f".{part}" if place else part
        if place in labels:
            place += f" ({labels[place]})"
    return place


# An invalid entry never became a model, so its name has to come from the raw document.
def _entry_labels(document: object, entries: _Entries | None) -> dict[str, str]:
    if entries is None or not isinstance(document, dict):
        return {}
    items = document.get(entries.list_field)
    if not isinstance(items, list):
        return {}
    labels: dict[str, str] = {}
    for index, item in enumerate(items):
        name = item.get(entries.name_field) if isinstance(item, dict) else None
        if isinstance(name, str) and name.strip():
            labels[f"{entries.list_field}[{index}]"] = name.strip()
    return labels
