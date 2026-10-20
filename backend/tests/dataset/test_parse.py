from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import inspect

from car_advisor.dataset.importer import DatasetImportError, parse_dataset
from car_advisor.dataset.models import UsageValueRow

HEADER = (
    "שנת מס ,שנת רישום,קוד סוג רכב ,קוד תוצר,תוצר           ,קוד דגם,תאור דגם,סוג דלק,"
    "כינוי מסחרי ,לא-0.כן-1 אוטומט ,1-חשמלי.2-היברידי.3-פלג-אין     ,ציון ירוק ,דרגת זיהום,"
    "נפח מנוע,משקל ,תאריך תחולה,מחיר מחירון,מחיר מתואם,שווי שימוש"
)

# A 2026 record, padded with spaces the way the file pads its fields.
FIELDS = {
    "tax_year": "2026",
    "registration_year": "2020",
    "vehicle_type_code": "1",
    "manufacturer_code": "01229",
    "manufacturer_name": "טסלה ארה''ב          ",
    "model_code": "00001",
    "model_description": "            3E7E       ",
    "fuel_type": "4",
    "trade_name": "             MODEL 3     ",
    "automatic": "1",
    "electric_raw": "3",
    "green_score": "000",
    "pollution_level": "01",
    "engine_cc": "00000",
    "weight": "02305",
    "effective_date": "06/01/2020",
    "list_price": " 322200  ",
    "adjusted_price": " 377764  ",
    "usage_value": "   7990                                        ",
}

PARSED: dict[str, object] = {
    "tax_year": 2026,
    "registration_year": 2020,
    "vehicle_type_code": "1",
    "manufacturer_code": "01229",
    "manufacturer_name": "טסלה ארה''ב",
    "model_code": "00001",
    "model_description": "3E7E",
    "fuel_type": "4",
    "trade_name": "MODEL 3",
    "automatic": 1,
    "electric_raw": 3,
    "green_score": 0,
    "pollution_level": 1,
    "engine_cc": 0,
    "weight": 2305,
    "effective_date": date(2020, 1, 6),
    "list_price": 322200,
    "adjusted_price": 377764,
    "usage_value": 7990,
    "powertrain": "electric",
}


def row(**changes: str) -> str:
    return ",".join({**FIELDS, **changes}.values())


SHORT_ROW = ",".join(list(FIELDS.values())[:-1])


def encode(*lines: str) -> bytes:
    return "".join(f"{line}\r\n" for line in lines).encode("windows-1255")


def write_dataset(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "usage-values.csv"
    path.write_bytes(encode(HEADER, *lines))
    return path


def test_fields_are_trimmed_and_typed(tmp_path: Path) -> None:
    rows = parse_dataset(write_dataset(tmp_path, row()), 2026)

    assert rows == [PARSED]
    assert rows[0].keys() == set(inspect(UsageValueRow).columns.keys())


def test_codes_that_differ_only_in_leading_zeros_are_different_cars(tmp_path: Path) -> None:
    path = write_dataset(tmp_path, row(), row(manufacturer_code="1229"), row(model_code="1"))

    rows = parse_dataset(path, 2026)

    assert [(parsed["manufacturer_code"], parsed["model_code"]) for parsed in rows] == [
        ("01229", "00001"),
        ("1229", "00001"),
        ("01229", "1"),
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("06/01/2020", date(2020, 1, 6), id="6-january"),
        pytest.param("13/02/2026", date(2026, 2, 13), id="day-above-12"),
        pytest.param("29/02/2024", date(2024, 2, 29), id="leap-day"),
    ],
)
def test_dates_are_read_day_first(tmp_path: Path, text: str, expected: date) -> None:
    [parsed] = parse_dataset(write_dataset(tmp_path, row(effective_date=text)), 2026)

    assert parsed["effective_date"] == expected


@pytest.mark.parametrize(
    ("electric", "powertrain"),
    [("3", "electric"), ("1", "hybrid"), ("2", "plug_in"), ("0", "conventional")],
)
def test_powertrain_comes_only_from_the_electric_column(
    tmp_path: Path, electric: str, powertrain: str
) -> None:
    # An engine size never overrides the electric column (Req 1.8).
    path = write_dataset(tmp_path, row(electric_raw=electric, engine_cc="01598"))

    [parsed] = parse_dataset(path, 2026)

    assert (parsed["electric_raw"], parsed["powertrain"]) == (int(electric), powertrain)


@pytest.mark.parametrize(
    ("bad_row", "reason"),
    [
        pytest.param(SHORT_ROW, "expected 19 columns, found 18", id="18-columns"),
        pytest.param(row() + ",0", "expected 19 columns, found 20", id="20-columns"),
        pytest.param(
            row(registration_year=""),
            "Official_Key part registration_year is empty",
            id="empty-year",
        ),
        pytest.param(
            row(model_code="     "), "Official_Key part model_code is empty", id="blank-code"
        ),
        pytest.param(
            row(manufacturer_code="01A29"),
            "manufacturer_code must contain only digits, got '01A29'",
            id="letter-in-code",
        ),
        pytest.param(
            row(usage_value=" 79a0 "),
            "usage_value is not a whole number: '79a0'",
            id="non-numeric-amount",
        ),
        pytest.param(
            row(list_price="322200.5"),
            "list_price is not a whole number: '322200.5'",
            id="fractional-amount",
        ),
        pytest.param(
            row(effective_date="31/02/2026"),
            "effective_date is not a valid dd/mm/yyyy date: '31/02/2026'",
            id="31-february",
        ),
        pytest.param(
            row(effective_date="2020-01-06"),
            "effective_date is not a valid dd/mm/yyyy date: '2020-01-06'",
            id="iso-date",
        ),
        pytest.param(
            row(electric_raw="4"), "electric_raw must be 0, 1, 2 or 3, got 4", id="electric-4"
        ),
        pytest.param(
            row(tax_year="2025"),
            "tax year 2025 differs from the tax year being loaded, 2026",
            id="other-tax-year",
        ),
    ],
)
def test_a_malformed_row_reports_its_line_and_reason(
    tmp_path: Path, bad_row: str, reason: str
) -> None:
    # Line 4 is malformed too; only the first malformed line is reported.
    path = write_dataset(tmp_path, row(), bad_row, SHORT_ROW)

    with pytest.raises(DatasetImportError) as caught:
        parse_dataset(path, 2026)

    assert (caught.value.path, caught.value.line, caught.value.reason) == (path, 3, reason)


def test_a_repeated_key_names_both_lines(tmp_path: Path) -> None:
    repeated = row(manufacturer_code="  01229  ", usage_value="8100")
    path = write_dataset(tmp_path, row(), row(model_code="00002"), repeated)

    with pytest.raises(DatasetImportError) as caught:
        parse_dataset(path, 2026)

    assert caught.value.line == 4
    assert str(caught.value) == (
        f"{path}, line 4: duplicate Official_Key 2026-2020-1-01229-00001, first seen on line 2"
    )


def test_a_missing_file_reports_the_path_and_reason(tmp_path: Path) -> None:
    path = tmp_path / "missing.csv"

    with pytest.raises(DatasetImportError) as caught:
        parse_dataset(path, 2026)

    assert (caught.value.path, caught.value.line, caught.value.reason) == (
        path,
        None,
        "file not found",
    )


def test_an_unreadable_path_reports_the_path_and_reason(tmp_path: Path) -> None:
    with pytest.raises(DatasetImportError) as caught:
        parse_dataset(tmp_path, 2026)

    assert (caught.value.path, caught.value.reason) == (
        tmp_path,
        "cannot read the file: Is a directory",
    )


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        pytest.param(
            encode(HEADER, row()).replace(b"3E7E", b"3E\xffE"),
            "cannot decode the file as windows-1255",
            id="undecodable",
        ),
        pytest.param(encode(HEADER), "no records after the header row", id="header-only"),
        pytest.param(b"", "no records after the header row", id="empty"),
    ],
)
def test_a_file_without_readable_records_reports_the_path_and_reason(
    tmp_path: Path, content: bytes, reason: str
) -> None:
    path = tmp_path / "usage-values.csv"
    path.write_bytes(content)

    with pytest.raises(DatasetImportError) as caught:
        parse_dataset(path, 2026)

    assert (caught.value.path, caught.value.line, caught.value.reason) == (path, None, reason)
    assert str(caught.value) == f"{path}: {reason}"
