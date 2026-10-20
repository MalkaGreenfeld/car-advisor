import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from car_advisor.dataset.importer import DatasetImportError, import_tax_year
from car_advisor.db import ensure_database, make_engine, make_session_factory, run_migrations
from car_advisor.settings import Settings, settings_error_fields


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="car-advisor-import",
        description=(
            "Reload one tax year of the usage value dataset into the database. "
            "The records that the database already holds for that year are replaced."
        ),
    )
    parser.add_argument(
        "--tax-year",
        type=int,
        required=True,
        metavar="YEAR",
        help="tax year to load, for example 2026",
    )
    parser.add_argument(
        "--path",
        type=Path,
        metavar="FILE",
        help="dataset CSV file in windows-1255 (default: DATASET_PATH from the settings)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as error:
        # The default message lists every input value, and the database password with them.
        print(f"invalid settings: {', '.join(settings_error_fields(error))}", file=sys.stderr)
        return 1

    path = settings.dataset_path if args.path is None else args.path
    ensure_database(settings)
    engine = make_engine(settings)
    try:
        run_migrations(engine)
        result = import_tax_year(make_session_factory(engine), path, args.tax_year)
    except DatasetImportError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print(f"Tax year {result.tax_year} loaded, record count: {result.record_count:,}")
    return 0
