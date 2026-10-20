from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.dataset.importer import ImportResult, has_records, import_tax_year
from car_advisor.dataset.models import UsageValueRow

# The parser skips the header row without reading it.
HEADER = "header"

PREVIOUS = {(2025, "00001", 7000), (2026, "00001", 7990), (2026, "00002", 8100)}


def record(tax_year: int, model_code: str, usage_value: int) -> str:
    return (
        f"{tax_year},2020,1,01229,טסלה ארה''ב,{model_code},3E7E,4,MODEL 3,1,3,000,01,00000,"
        f"02305,06/01/2020,322200,377764,{usage_value}"
    )


def write_dataset(path: Path, *records: str) -> Path:
    path.write_bytes("".join(f"{line}\r\n" for line in (HEADER, *records)).encode("windows-1255"))
    return path


def stored(sessions: sessionmaker[Session]) -> set[tuple[int, str, int]]:
    query = select(UsageValueRow.tax_year, UsageValueRow.model_code, UsageValueRow.usage_value)
    with sessions() as session:
        return set(session.execute(query).all())


@pytest.fixture
def seeded(tmp_path: Path, sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    other_year = write_dataset(tmp_path / "2025.csv", record(2025, "00001", 7000))
    this_year = write_dataset(
        tmp_path / "2026.csv", record(2026, "00001", 7990), record(2026, "00002", 8100)
    )
    import_tax_year(sessions, other_year, 2025)
    import_tax_year(sessions, this_year, 2026)
    return sessions


def test_the_real_file_imports_77988_records_for_2026(
    real_import: ImportResult, real_sessions: sessionmaker[Session]
) -> None:
    count = select(func.count()).select_from(UsageValueRow).where(UsageValueRow.tax_year == 2026)
    with real_sessions() as session:
        stored_count = session.scalar(count)

    assert real_import == ImportResult(tax_year=2026, record_count=77_988)
    assert stored_count == 77_988
    assert has_records(real_sessions, 2026)
    assert not has_records(real_sessions, 2025)


def test_a_reload_replaces_that_tax_year_only(
    tmp_path: Path, seeded: sessionmaker[Session]
) -> None:
    reload = write_dataset(
        tmp_path / "reload.csv", record(2026, "00002", 8200), record(2026, "00003", 9000)
    )

    result = import_tax_year(seeded, reload, 2026)

    assert result == ImportResult(tax_year=2026, record_count=2)
    assert stored(seeded) == {
        (2025, "00001", 7000),
        (2026, "00002", 8200),
        (2026, "00003", 9000),
    }


def test_a_failed_write_keeps_the_previous_records(
    tmp_path: Path, db_engine: Engine, seeded: sessionmaker[Session]
) -> None:
    # The database itself rejects record 00004, after the delete of 2026 has run.
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_00004 BEFORE INSERT ON usage_values"
                " WHEN NEW.model_code = '00004' BEGIN SELECT RAISE(ABORT, 'write rejected'); END"
            )
        )
    reload = write_dataset(
        tmp_path / "reload.csv", record(2026, "00003", 9000), record(2026, "00004", 9100)
    )

    with pytest.raises(IntegrityError, match="write rejected"):
        import_tax_year(seeded, reload, 2026)

    assert stored(seeded) == PREVIOUS
