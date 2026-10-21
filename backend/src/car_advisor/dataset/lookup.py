import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import ColumnElement, Row, and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.dataset.models import UsageValueRow
from car_advisor.engine.config import CarCatalog, CatalogCar
from car_advisor.engine.types import CarKey, CarRecord, Powertrain

type KeyPart = str | int | None

_DIGITS = re.compile(r"[0-9]+")

# Five parameters per key, against SQL Server's limit of 2,100 parameters per statement.
_KEYS_PER_QUERY = 100

_LIKE_ESCAPE = "\\"
# SQL Server's LIKE also reads [ as the start of a character class.
_LIKE_SPECIALS = str.maketrans({char: _LIKE_ESCAPE + char for char in f"%_[{_LIKE_ESCAPE}"})

# Selecting columns instead of entities keeps a search that matches thousands of rows well
# inside its 500 ms budget.
_CANDIDATE_COLUMNS = (
    UsageValueRow.tax_year,
    UsageValueRow.registration_year,
    UsageValueRow.vehicle_type_code,
    UsageValueRow.manufacturer_code,
    UsageValueRow.model_code,
    UsageValueRow.manufacturer_name,
    UsageValueRow.trade_name,
    UsageValueRow.model_description,
    UsageValueRow.engine_cc,
    UsageValueRow.powertrain,
    UsageValueRow.list_price,
)
_RECORD_COLUMNS = (*_CANDIDATE_COLUMNS, UsageValueRow.adjusted_price, UsageValueRow.usage_value)


@dataclass(frozen=True)
class KeyParts:
    tax_year: KeyPart = None
    registration_year: KeyPart = None
    vehicle_type_code: KeyPart = None
    manufacturer_code: KeyPart = None
    model_code: KeyPart = None


@dataclass(frozen=True)
class CarCandidate:
    key: CarKey
    manufacturer_name: str
    trade_name: str
    model_code: str
    model_description: str
    vehicle_type_code: str
    engine_cc: int
    powertrain: Powertrain
    list_price: int


@dataclass(frozen=True)
class Found:
    record: CarRecord


@dataclass(frozen=True)
class NotFound:
    key: CarKey


@dataclass(frozen=True)
class Rejected:
    # Inputs that are absent, blank or, for the key parts, not made of digits.
    missing: tuple[str, ...]


@dataclass(frozen=True)
class LookupFailed:
    reason: str


@dataclass(frozen=True)
class Candidates:
    items: tuple[CarCandidate, ...]
    selection_required: bool
    none_found: bool


def _escape_like(text: str) -> str:
    return text.translate(_LIKE_SPECIALS)


def _text(part: KeyPart) -> str:
    return "" if part is None else str(part).strip()


def _failed(error: SQLAlchemyError) -> LookupFailed:
    # The message of a database error can carry the SQL and its parameters, so only the type
    # is reported.
    return LookupFailed(f"cannot read the Usage_Value_Dataset: {type(error).__name__}")


def _matches(key: CarKey) -> ColumnElement[bool]:
    return and_(
        UsageValueRow.tax_year == key.tax_year,
        UsageValueRow.registration_year == key.registration_year,
        UsageValueRow.vehicle_type_code == key.vehicle_type_code,
        UsageValueRow.manufacturer_code == key.manufacturer_code,
        UsageValueRow.model_code == key.model_code,
    )


def _key(row: Row[Any]) -> CarKey:
    return CarKey(
        tax_year=row.tax_year,
        registration_year=row.registration_year,
        vehicle_type_code=row.vehicle_type_code,
        manufacturer_code=row.manufacturer_code,
        model_code=row.model_code,
    )


def _record(row: Row[Any]) -> CarRecord:
    return CarRecord(
        key=_key(row),
        manufacturer_name=row.manufacturer_name,
        trade_name=row.trade_name,
        model_description=row.model_description,
        engine_cc=row.engine_cc,
        powertrain=Powertrain(row.powertrain),
        list_price=row.list_price,
        adjusted_price=row.adjusted_price,
        usage_value=row.usage_value,
    )


def _candidate(row: Row[Any]) -> CarCandidate:
    return CarCandidate(
        key=_key(row),
        manufacturer_name=row.manufacturer_name,
        trade_name=row.trade_name,
        model_code=row.model_code,
        model_description=row.model_description,
        vehicle_type_code=row.vehicle_type_code,
        engine_cc=row.engine_cc,
        powertrain=Powertrain(row.powertrain),
        list_price=row.list_price,
    )


class CarLookup:
    def __init__(self, sessions: sessionmaker[Session], tax_year: int) -> None:
        self._sessions = sessions
        self.tax_year = tax_year

    def get(self, parts: KeyParts) -> Found | NotFound | Rejected | LookupFailed:
        text = {name: _text(part) for name, part in asdict(parts).items()}
        unusable = tuple(name for name, value in text.items() if not _DIGITS.fullmatch(value))
        if unusable:
            return Rejected(unusable)

        key = CarKey(
            tax_year=int(text["tax_year"]),
            registration_year=int(text["registration_year"]),
            vehicle_type_code=text["vehicle_type_code"],
            manufacturer_code=text["manufacturer_code"],
            model_code=text["model_code"],
        )
        try:
            record = self._fetch([key]).get(key)
        except SQLAlchemyError as error:
            return _failed(error)
        return NotFound(key) if record is None else Found(record)

    # get_many and missing_keys have no result type for a failure, so a database error
    # reaches their caller as it is.
    def get_many(self, keys: Iterable[CarKey]) -> dict[CarKey, CarRecord]:
        return self._fetch(list(dict.fromkeys(keys)))

    def missing_keys(self, keys: Iterable[CarKey]) -> list[CarKey]:
        wanted = list(dict.fromkeys(keys))
        found = self._fetch(wanted)
        return [key for key in wanted if key not in found]

    def search(
        self, registration_year: int | None, text: str | None
    ) -> Candidates | Rejected | LookupFailed:
        needle = "" if text is None else text.strip()
        if registration_year is None or not needle:
            absent = (("registration_year", registration_year is None), ("text", not needle))
            return Rejected(tuple(name for name, is_absent in absent if is_absent))

        # UPPER on both sides makes the match ignore Latin case whatever the collation.
        pattern = f"%{_escape_like(needle.upper())}%"
        query = (
            select(*_CANDIDATE_COLUMNS)
            .where(
                UsageValueRow.tax_year == self.tax_year,
                UsageValueRow.registration_year == registration_year,
                or_(
                    func.upper(UsageValueRow.manufacturer_name).like(pattern, escape=_LIKE_ESCAPE),
                    func.upper(UsageValueRow.trade_name).like(pattern, escape=_LIKE_ESCAPE),
                ),
            )
            # The rest of the key breaks ties, so the order never changes between runs.
            .order_by(
                UsageValueRow.manufacturer_name,
                UsageValueRow.trade_name,
                UsageValueRow.model_code,
                UsageValueRow.vehicle_type_code,
                UsageValueRow.manufacturer_code,
            )
        )
        try:
            with self._sessions() as session:
                items = tuple(_candidate(row) for row in session.execute(query))
        except SQLAlchemyError as error:
            return _failed(error)
        return Candidates(items, selection_required=len(items) > 1, none_found=not items)

    def _fetch(self, keys: list[CarKey]) -> dict[CarKey, CarRecord]:
        records: dict[CarKey, CarRecord] = {}
        with self._sessions() as session:
            for start in range(0, len(keys), _KEYS_PER_QUERY):
                chunk = keys[start : start + _KEYS_PER_QUERY]
                query = select(*_RECORD_COLUMNS).where(or_(*(_matches(key) for key in chunk)))
                for row in session.execute(query):
                    record = _record(row)
                    records[record.key] = record
        return records


class CatalogMismatch(Exception):
    def __init__(self, missing: Sequence[CatalogCar]) -> None:
        lines = "".join(f"\n  {car.display_name}: {car.official_key}" for car in missing)
        super().__init__(
            f"Car_Catalog cars not found in the Usage_Value_Dataset by Official_Key:{lines}"
        )
        self.missing = list(missing)


@dataclass(frozen=True)
class UsageValueMismatch:
    """A catalog car whose usage value differs from the one of its dataset record (Req 4.19)."""

    display_name: str
    car_key: CarKey
    catalog_usage_value: int
    dataset_usage_value: int


def check_catalog(lookup: CarLookup, catalog: CarCatalog) -> list[UsageValueMismatch]:
    """Check every catalog car against the dataset.

    Raises a CatalogMismatch that lists every car whose key is not in the dataset (Req 4.7).
    Otherwise returns the cars whose usage value differs from their record, in catalog order. That
    is only a warning: every calculation uses the dataset's value (Req 4.18).
    """
    records = lookup.get_many(car.official_key for car in catalog.cars)
    missing = [car for car in catalog.cars if car.official_key not in records]
    if missing:
        raise CatalogMismatch(missing)
    return [
        UsageValueMismatch(
            display_name=car.display_name,
            car_key=car.official_key,
            catalog_usage_value=car.usage_value,
            dataset_usage_value=records[car.official_key].usage_value,
        )
        for car in catalog.cars
        if car.usage_value is not None and car.usage_value != records[car.official_key].usage_value
    ]
