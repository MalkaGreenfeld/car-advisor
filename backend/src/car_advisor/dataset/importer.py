import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.dataset.models import UsageValueRow
from car_advisor.engine.types import CarKey, Powertrain

_COLUMNS = (
    "tax_year",
    "registration_year",
    "vehicle_type_code",
    "manufacturer_code",
    "manufacturer_name",
    "model_code",
    "model_description",
    "fuel_type",
    "trade_name",
    "automatic",
    "electric_raw",
    "green_score",
    "pollution_level",
    "engine_cc",
    "weight",
    "effective_date",
    "list_price",
    "adjusted_price",
    "usage_value",
)
_KEY_FIELDS = (
    "tax_year",
    "registration_year",
    "vehicle_type_code",
    "manufacturer_code",
    "model_code",
)
_CODE_FIELDS = frozenset({"vehicle_type_code", "manufacturer_code", "model_code"})
_INTEGER_FIELDS = frozenset(
    {
        "tax_year",
        "registration_year",
        "automatic",
        "electric_raw",
        "green_score",
        "pollution_level",
        "engine_cc",
        "weight",
        "list_price",
        "adjusted_price",
        "usage_value",
    }
)

# int() alone would also accept "1_000", "+5" and non-ASCII digits.
_DIGITS = re.compile(r"[0-9]+")
_INTEGER = re.compile(r"-?[0-9]+")

# The file's actual values; the legend in the column header (1 electric, 2 hybrid, 3 plug-in)
# is wrong.
_POWERTRAINS = {
    3: Powertrain.ELECTRIC,
    1: Powertrain.HYBRID,
    2: Powertrain.PLUG_IN,
    0: Powertrain.CONVENTIONAL,
}


class DatasetImportError(Exception):
    def __init__(self, path: Path, reason: str, line: int | None = None) -> None:
        location = str(path) if line is None else f"{path}, line {line}"
        super().__init__(f"{location}: {reason}")
        self.path = path
        self.reason = reason
        self.line = line


@dataclass(frozen=True)
class ImportResult:
    tax_year: int
    record_count: int


def derive_powertrain(electric_raw: int) -> Powertrain:
    if electric_raw not in _POWERTRAINS:
        raise ValueError(f"electric_raw must be 0, 1, 2 or 3, got {electric_raw}")
    return _POWERTRAINS[electric_raw]


def parse_dataset(path: Path, tax_year: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    first_lines: dict[CarKey, int] = {}
    try:
        with path.open(encoding="windows-1255", newline="") as file:
            reader = csv.reader(file)
            next(reader, None)
            for fields in reader:
                line = reader.line_num
                try:
                    key, row = _parse_row(fields, tax_year)
                except ValueError as exc:
                    raise DatasetImportError(path, str(exc), line) from None
                if key in first_lines:
                    reason = f"duplicate Official_Key {key}, first seen on line {first_lines[key]}"
                    raise DatasetImportError(path, reason, line)
                first_lines[key] = line
                rows.append(row)
    except FileNotFoundError as exc:
        raise DatasetImportError(path, "file not found") from exc
    except UnicodeDecodeError as exc:
        raise DatasetImportError(path, "cannot decode the file as windows-1255") from exc
    except OSError as exc:
        raise DatasetImportError(path, f"cannot read the file: {exc.strerror}") from exc
    if not rows:
        raise DatasetImportError(path, "no records after the header row")
    return rows


# A ValueError carries the reason of the first failed check; parse_dataset adds the line.
def _parse_row(fields: list[str], tax_year: int) -> tuple[CarKey, dict[str, object]]:
    if len(fields) != len(_COLUMNS):
        raise ValueError(f"expected {len(_COLUMNS)} columns, found {len(fields)}")
    text = dict(zip(_COLUMNS, (field.strip() for field in fields), strict=True))

    for name in _KEY_FIELDS:
        if not text[name]:
            raise ValueError(f"Official_Key part {name} is empty")

    numbers: dict[str, int] = {}
    for name in _COLUMNS:
        value = text[name]
        if name in _CODE_FIELDS and not _DIGITS.fullmatch(value):
            raise ValueError(f"{name} must contain only digits, got {value!r}")
        if name in _INTEGER_FIELDS:
            if not _INTEGER.fullmatch(value):
                raise ValueError(f"{name} is not a whole number: {value!r}")
            numbers[name] = int(value)

    try:
        effective_date = datetime.strptime(text["effective_date"], "%d/%m/%Y").date()
    except ValueError:
        raise ValueError(
            f"effective_date is not a valid dd/mm/yyyy date: {text['effective_date']!r}"
        ) from None

    powertrain = derive_powertrain(numbers["electric_raw"])

    if numbers["tax_year"] != tax_year:
        raise ValueError(
            f"tax year {numbers['tax_year']} differs from the tax year being loaded, {tax_year}"
        )

    key = CarKey(
        tax_year=numbers["tax_year"],
        registration_year=numbers["registration_year"],
        vehicle_type_code=text["vehicle_type_code"],
        manufacturer_code=text["manufacturer_code"],
        model_code=text["model_code"],
    )
    row: dict[str, object] = {
        **text,
        **numbers,
        "effective_date": effective_date,
        "powertrain": powertrain.value,
    }
    return key, row


def import_tax_year(sessions: sessionmaker[Session], path: Path, tax_year: int) -> ImportResult:
    # Parsing the whole file first means a bad file fails before anything is deleted, and a
    # failure during the insert rolls the delete back with it (Req 1.11-1.12).
    rows = parse_dataset(path, tax_year)
    with sessions.begin() as session:
        session.execute(delete(UsageValueRow).where(UsageValueRow.tax_year == tax_year))
        session.execute(insert(UsageValueRow), rows)
    return ImportResult(tax_year=tax_year, record_count=len(rows))


def has_records(sessions: sessionmaker[Session], tax_year: int) -> bool:
    query = select(UsageValueRow.tax_year).where(UsageValueRow.tax_year == tax_year).limit(1)
    with sessions() as session:
        return session.scalars(query).first() is not None
