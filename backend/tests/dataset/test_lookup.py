import time
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.dataset.importer import import_tax_year
from car_advisor.dataset.lookup import (
    Candidates,
    CarCandidate,
    CarLookup,
    Found,
    KeyParts,
    LookupFailed,
    NotFound,
    Rejected,
)
from car_advisor.engine.types import CarKey, CarRecord, Powertrain

# Every expected value below was read from data/usage-values-2026.csv itself, not through the
# importer.
MODEL_3_KEY = CarKey.parse("2026-2023-1-01229-00177")
MODEL_3_PARTS = KeyParts(2026, 2023, "1", "01229", "00177")
MODEL_3 = CarRecord(
    key=MODEL_3_KEY,
    manufacturer_name="טסלה ארה''ב",
    trade_name="MODEL 3",
    model_description="3E7E",
    engine_cc=0,
    powertrain=Powertrain.ELECTRIC,
    list_price=319000,
    adjusted_price=349006,
    usage_value=7280,
)

NO_CANDIDATES = Candidates((), selection_required=False, none_found=True)


def nissan_pathfinder(
    vehicle_type_code: str, model_code: str, description: str, engine_cc: int, list_price: int
) -> CarCandidate:
    return CarCandidate(
        key=CarKey.parse(f"2026-2012-{vehicle_type_code}-00634-{model_code}"),
        manufacturer_name="ניסאן ספרד",
        trade_name="PATHFINDER",
        model_code=model_code,
        model_description=description,
        vehicle_type_code=vehicle_type_code,
        engine_cc=engine_cc,
        powertrain=Powertrain.CONVENTIONAL,
        list_price=list_price,
    )


# The four Pathfinder records of registration year 2012, in manufacturer, trade name and
# model code order.
PATHFINDERS_2012 = (
    nissan_pathfinder("1", "00031", "JVWR51", 2488, 289900),
    nissan_pathfinder("2", "00038", "JVWR51", 2488, 289900),
    nissan_pathfinder("1", "00041", "JVWR51", 2488, 329900),
    nissan_pathfinder("1", "00051", "JAWR51", 2991, 349900),
)

# 2022 is the busiest registration year, with 7,777 records, and 4,058 of them contain an "A".
BUSIEST_SEARCH = (2022, "A")
BUSIEST_SEARCH_MATCHES = 4058

# The file has no name with a %, [ or \, so these come from a small file of our own. AXB is the
# name that a wildcard reading of %, _ or [ would match as well.
SPECIAL_NAMES = {"percent": "A%B", "underscore": "A_B", "bracket": "A[XY]B", "backslash": "A\\B"}
SPECIAL_NAME_PARAMS = [pytest.param(name, id=label) for label, name in SPECIAL_NAMES.items()]
SEARCHABLE_NAMES = (*SPECIAL_NAMES.values(), "AXB")


def timed[T](call: Callable[[], T]) -> tuple[T, float]:
    # The first call is a warm-up: it compiles the statement and opens the connection. Then the
    # fastest of three calls counts, because another process on a busy machine slows a single call
    # down, while a lookup that is really too slow is slow every time.
    call()
    times = []
    for _ in range(3):
        started = time.perf_counter()
        result = call()
        times.append(time.perf_counter() - started)
    return result, min(times)


def candidates(result: Candidates | Rejected | LookupFailed) -> Candidates:
    assert isinstance(result, Candidates), result
    return result


def import_searchable_names(
    sessions: sessionmaker[Session], directory: Path, tax_year: int
) -> None:
    lines = [
        f"{tax_year},2020,1,01229,טסלה ארה''ב,{number:05d},3E7E,4,{name},1,3,000,01,00000,02305,"
        "06/01/2020,322200,377764,7990"
        for number, name in enumerate(SEARCHABLE_NAMES, start=1)
    ]
    path = directory / f"names-{tax_year}.csv"
    path.write_bytes("".join(f"{line}\r\n" for line in ("header", *lines)).encode("windows-1255"))
    import_tax_year(sessions, path, tax_year)


@pytest.fixture(scope="module")
def lookup(real_sessions: sessionmaker[Session]) -> CarLookup:
    return CarLookup(real_sessions, 2026)


@pytest.mark.parametrize(
    "parts",
    [
        pytest.param(MODEL_3_PARTS, id="exact"),
        pytest.param(KeyParts(" 2026 ", "2023\t", " 1", "01229 ", "\n00177"), id="padded"),
    ],
)
def test_a_full_key_returns_exactly_that_record_with_its_usage_value(
    lookup: CarLookup, parts: KeyParts
) -> None:
    assert lookup.get(parts) == Found(MODEL_3)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("2025-2023-1-01229-00177", id="tax_year"),
        pytest.param("2026-2024-1-01229-00177", id="registration_year"),
        pytest.param("2026-2023-2-01229-00177", id="vehicle_type_code"),
        pytest.param("2026-2023-1-01230-00177", id="manufacturer_code"),
        pytest.param("2026-2023-1-01229-00178", id="model_code"),
    ],
)
def test_a_key_that_differs_in_one_part_is_not_found(lookup: CarLookup, text: str) -> None:
    key = CarKey.parse(text)

    assert lookup.get(KeyParts(**key.model_dump())) == NotFound(key)


def test_a_key_with_absent_or_blank_parts_is_rejected_naming_every_one(lookup: CarLookup) -> None:
    parts = KeyParts(
        tax_year=2026, registration_year="  ", manufacturer_code="01229", model_code=""
    )

    assert lookup.get(parts) == Rejected(("registration_year", "vehicle_type_code", "model_code"))
    assert lookup.get(KeyParts()) == Rejected(
        ("tax_year", "registration_year", "vehicle_type_code", "manufacturer_code", "model_code")
    )


def test_a_key_part_that_is_not_a_number_is_rejected(lookup: CarLookup) -> None:
    parts = KeyParts(2026, "20x3", "1", "01229", "00177")

    assert lookup.get(parts) == Rejected(("registration_year",))


@pytest.mark.parametrize("text", ["pathfinder", "  PathFinder  "])
def test_a_trade_name_alone_returns_every_matching_candidate(lookup: CarLookup, text: str) -> None:
    result = lookup.search(2012, text)

    assert result == Candidates(PATHFINDERS_2012, selection_required=True, none_found=False)


def test_a_single_match_does_not_require_a_selection(lookup: CarLookup) -> None:
    result = candidates(lookup.search(2010, "mx_5"))

    assert [candidate.model_code for candidate in result.items] == ["00012"]
    assert (result.selection_required, result.none_found) == (False, False)


def test_a_manufacturer_name_matches_too(lookup: CarLookup) -> None:
    result = candidates(lookup.search(2012, "ניסאן ספרד"))

    assert {candidate.manufacturer_name for candidate in result.items} == {"ניסאן ספרד"}
    assert set(PATHFINDERS_2012) <= set(result.items)


def test_a_record_that_matches_in_both_names_is_returned_once(lookup: CarLookup) -> None:
    # In 2010, 194 manufacturer names and 48 trade names contain a hyphen, 9 records in both.
    result = candidates(lookup.search(2010, "-"))

    keys = {candidate.key for candidate in result.items}
    assert len(result.items) == len(keys) == 233


def test_candidates_are_ordered_by_manufacturer_trade_name_and_model_code(
    lookup: CarLookup,
) -> None:
    result = candidates(lookup.search(2010, "-"))

    # SQLite orders text by code point, as sorted() does.
    order = [(item.manufacturer_name, item.trade_name, item.model_code) for item in result.items]
    assert order == sorted(order)


@pytest.mark.parametrize(
    ("registration_year", "text"),
    [
        pytest.param(2012, "no such car", id="no such name"),
        pytest.param(2012, "MODEL 3", id="another registration year"),
    ],
)
def test_a_search_with_no_match_returns_an_empty_list(
    lookup: CarLookup, registration_year: int, text: str
) -> None:
    assert lookup.search(registration_year, text) == NO_CANDIDATES


def test_a_search_covers_only_the_tax_year_of_the_lookup(
    real_sessions: sessionmaker[Session],
) -> None:
    assert CarLookup(real_sessions, 2025).search(2012, "pathfinder") == NO_CANDIDATES


@pytest.mark.parametrize(
    ("registration_year", "text", "missing"),
    [
        pytest.param(None, "pathfinder", ("registration_year",), id="no year"),
        pytest.param(2012, None, ("text",), id="no text"),
        pytest.param(2012, " \t ", ("text",), id="blank text"),
        pytest.param(None, "", ("registration_year", "text"), id="neither"),
    ],
)
def test_a_search_without_a_year_or_a_text_is_rejected(
    lookup: CarLookup, registration_year: int | None, text: str | None, missing: tuple[str, ...]
) -> None:
    assert lookup.search(registration_year, text) == Rejected(missing)


def test_percent_and_underscore_in_the_text_match_literally(lookup: CarLookup) -> None:
    assert lookup.search(2012, "PATH%FINDER") == NO_CANDIDATES
    assert lookup.search(2012, "PATH_FINDER") == NO_CANDIDATES
    assert lookup.search(2010, "%") == NO_CANDIDATES

    underscored = candidates(lookup.search(2010, "_"))
    assert len(underscored.items) == 13
    assert all("_" in candidate.trade_name for candidate in underscored.items)


@pytest.mark.parametrize("name", SPECIAL_NAME_PARAMS)
def test_special_characters_in_the_text_match_only_themselves(
    sessions: sessionmaker[Session], tmp_path: Path, name: str
) -> None:
    import_searchable_names(sessions, tmp_path, 2026)

    result = candidates(CarLookup(sessions, 2026).search(2020, name))

    assert [candidate.trade_name for candidate in result.items] == [name]


def test_get_many_returns_the_stored_records_and_missing_keys_names_the_rest(
    lookup: CarLookup,
) -> None:
    pathfinder = PATHFINDERS_2012[1].key
    absent = CarKey.parse("2026-2023-1-01229-00178")
    keys = [pathfinder, absent, MODEL_3_KEY, MODEL_3_KEY]

    found = lookup.get_many(keys)

    assert set(found) == {pathfinder, MODEL_3_KEY}
    assert found[MODEL_3_KEY] == MODEL_3
    assert lookup.missing_keys(keys) == [absent]


def test_more_keys_than_one_query_holds_are_all_looked_up(lookup: CarLookup) -> None:
    year, text = BUSIEST_SEARCH
    keys = [candidate.key for candidate in candidates(lookup.search(year, text)).items][:250]
    absent = CarKey.parse("2026-2023-1-01229-00178")

    assert set(lookup.get_many(keys)) == set(keys)
    assert lookup.missing_keys([*keys, absent]) == [absent]


def test_an_unreachable_database_gives_lookup_failed(tmp_path: Path) -> None:
    # SQLite cannot create a database file inside a directory that does not exist.
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'missing' / 'cars.db'}")
    unreachable = CarLookup(sessionmaker(engine), 2026)

    try:
        assert isinstance(unreachable.get(MODEL_3_PARTS), LookupFailed)
        assert isinstance(unreachable.search(2012, "pathfinder"), LookupFailed)
        with pytest.raises(SQLAlchemyError):
            unreachable.get_many([MODEL_3_KEY])
        with pytest.raises(SQLAlchemyError):
            unreachable.missing_keys([MODEL_3_KEY])
    finally:
        engine.dispose()


def test_a_key_lookup_takes_under_100_ms(lookup: CarLookup) -> None:
    result, seconds = timed(lambda: lookup.get(MODEL_3_PARTS))

    assert result == Found(MODEL_3)
    assert seconds < 0.1


def test_a_search_takes_under_500_ms(lookup: CarLookup) -> None:
    year, text = BUSIEST_SEARCH

    result, seconds = timed(lambda: lookup.search(year, text))

    assert len(candidates(result).items) == BUSIEST_SEARCH_MATCHES
    assert seconds < 0.5
