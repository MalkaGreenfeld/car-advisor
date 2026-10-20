import ast
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import NamedTuple

import pytest

PACKAGE = "car_advisor"
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"

LAYERS = ("engine", "settings", "logs", "db", "rules", "dataset", "conversations", "agent", "api")


def all_layers_except(*kept: str) -> tuple[str, ...]:
    return tuple(layer for layer in LAYERS if layer not in kept)


# The "Never imports" column of the module dependency table in design.md. The api layer may
# import every other layer, so it has no row.
NEVER_IMPORTS: dict[str, tuple[str, ...]] = {
    "engine": all_layers_except("engine"),
    "settings": all_layers_except("settings"),
    "logs": all_layers_except("logs", "settings"),
    "db": ("engine", "agent", "api"),
    "rules": ("db", "dataset", "agent", "api"),
    "dataset": ("conversations", "agent", "api"),
    "conversations": ("agent", "api"),
    "agent": ("api",),
}

# Budgets, tax bracket edges, thresholds and ceilings: rules.yaml is the only place for them.
DENYLIST = frozenset(
    {3500, 3700, 5200, 6000, 7010, 10060, 16150, 22440, 46690, 60130, 7522, 50695, 9500, 15712}
)


class Hit(NamedTuple):
    line: int
    text: str


def is_within(name: str, package: str) -> bool:
    return name == package or name.startswith(f"{package}.")


def engine_forbids(name: str) -> bool:
    top_level = name.partition(".")[0]
    return not (
        top_level in sys.stdlib_module_names
        or top_level == "pydantic"
        or is_within(name, f"{PACKAGE}.engine")
    )


def imports_layer(name: str, layers: Iterable[str]) -> bool:
    return any(is_within(name, f"{PACKAGE}.{layer}") for layer in layers)


def absolute_module(node: ast.ImportFrom, package: tuple[str, ...]) -> str:
    kept = package[: len(package) - node.level + 1] if node.level else ()
    return ".".join([*kept, *([node.module] if node.module else [])])


def imports_in(source: str, file: Path) -> list[Hit]:
    """Name everything the source imports, wherever the statement sits.

    `file` is relative to the source root; relative imports are resolved from its package.
    """
    package = file.parent.parts
    hits: list[Hit] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            hits += [Hit(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            # `from package import name` may import the submodule package.name, so the layer
            # checks need the whole dotted name.
            base = absolute_module(node, package)
            hits += [Hit(node.lineno, f"{base}.{alias.name}") for alias in node.names]
    return sorted(hits)


def is_denied(value: object) -> bool:
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return False
    return isinstance(value, int | float) and value in DENYLIST


def denied_numbers_in(source: str) -> list[Hit]:
    """Find numeric constants, and strings that spell a number, equal to a denylisted value."""
    return sorted(
        Hit(node.lineno, ast.unparse(node))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and is_denied(node.value)
    )


def forbidden_imports(
    sources: Mapping[Path, str], is_forbidden: Callable[[str], bool]
) -> list[str]:
    return [
        f"{file}:{hit.line}: {hit.text}"
        for file, source in sources.items()
        for hit in imports_in(source, file)
        if is_forbidden(hit.text)
    ]


def denied_numbers(sources: Mapping[Path, str]) -> list[str]:
    return [
        f"{file}:{hit.line}: {hit.text}"
        for file, source in sources.items()
        for hit in denied_numbers_in(source)
    ]


def module_files(layer: str) -> list[Path]:
    """List the layer's files relative to the source root; none while the layer is unbuilt."""
    package_files = (SOURCE_ROOT / PACKAGE / layer).rglob("*.py")
    module_file = SOURCE_ROOT / PACKAGE / f"{layer}.py"
    return sorted(
        path.relative_to(SOURCE_ROOT) for path in [*package_files, module_file] if path.is_file()
    )


def sources_of(files: Iterable[Path]) -> dict[Path, str]:
    return {file: (SOURCE_ROOT / file).read_text(encoding="utf-8") for file in files}


ENGINE_FILE = Path(PACKAGE) / "engine" / "payroll.py"


def test_engine_scan_flags_every_kind_of_forbidden_import() -> None:
    source = (
        "from decimal import Decimal\n"
        "from pydantic import BaseModel\n"
        "from .money import percent_of\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from sqlalchemy import Engine\n"
        "def load() -> None:\n"
        "    import yaml\n"
        "    from ..dataset import lookup\n"
        "    from pydantic_settings import BaseSettings\n"
    )

    assert forbidden_imports({ENGINE_FILE: source}, engine_forbids) == [
        "car_advisor/engine/payroll.py:6: sqlalchemy.Engine",
        "car_advisor/engine/payroll.py:8: yaml",
        "car_advisor/engine/payroll.py:9: car_advisor.dataset.lookup",
        "car_advisor/engine/payroll.py:10: pydantic_settings.BaseSettings",
    ]


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("import xml.dom as dom", "xml.dom"),
        ("from car_advisor import agent", "car_advisor.agent"),
        ("from .money import percent_of", "car_advisor.engine.money.percent_of"),
        ("from . import money", "car_advisor.engine.money"),
        ("from ..dataset import lookup", "car_advisor.dataset.lookup"),
        ("from .. import db", "car_advisor.db"),
    ],
)
def test_import_scan_names_what_each_statement_imports(statement: str, expected: str) -> None:
    assert imports_in(statement, ENGINE_FILE) == [Hit(1, expected)]


def test_relative_imports_in_a_package_init_count_from_the_package_itself() -> None:
    init = Path(PACKAGE) / "engine" / "__init__.py"

    assert imports_in("from .types import Group", init) == [
        Hit(1, "car_advisor.engine.types.Group")
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("car_advisor.api", True),
        ("car_advisor.api.app.create_app", True),
        ("car_advisor.apiary.app", False),
        ("car_advisor.dataset.api", False),
        ("api.app", False),
    ],
)
def test_layer_match_is_by_whole_package_name(name: str, expected: bool) -> None:
    assert imports_layer(name, ["api"]) is expected


def test_denylist_scan_finds_numeric_constants_and_numeric_strings() -> None:
    source = (
        "budget = 3500\n"
        "ceiling = 6_000.0\n"
        "edge = Decimal('22440')\n"
        "def pay(base: int = 7010) -> int:\n"
        "    return min(base, cap=16150)\n"
    )

    assert denied_numbers_in(source) == [
        Hit(1, "3500"),
        Hit(2, "6000.0"),
        Hit(3, "'22440'"),
        Hit(4, "7010"),
        Hit(5, "16150"),
    ]


def test_denylist_scan_ignores_comments_docstrings_text_and_other_numbers() -> None:
    source = (
        '"""The budget of 3500 comes from rules.yaml."""\n'
        "# 6000 is the Family_Track car cost\n"
        "text = 'up to 5200 per month'\n"
        "near = [3499, 3500.5, '3500x', 35000]\n"
    )

    assert denied_numbers_in(source) == []


def test_engine_modules_import_only_the_standard_library_and_pydantic() -> None:
    sources = sources_of(module_files("engine"))

    assert sources
    assert forbidden_imports(sources, engine_forbids) == []


@pytest.mark.parametrize("layer", list(NEVER_IMPORTS))
def test_layer_avoids_the_layers_the_dependency_table_forbids(layer: str) -> None:
    never = NEVER_IMPORTS[layer]

    # A layer that is not built yet has no files, so its row passes until the layer appears.
    sources = sources_of(module_files(layer))
    assert forbidden_imports(sources, lambda name: imports_layer(name, never)) == []


def test_the_dependency_table_matches_the_layers_on_disk() -> None:
    on_disk = {
        path.stem
        for path in (SOURCE_ROOT / PACKAGE).iterdir()
        if path.is_dir() or path.suffix == ".py"
    } - {"__init__", "__pycache__"}
    named = set(NEVER_IMPORTS).union(*NEVER_IMPORTS.values())

    assert on_disk <= set(LAYERS)
    assert named <= set(LAYERS)


def test_no_rule_number_is_hard_coded_in_the_source() -> None:
    files = sorted(path.relative_to(SOURCE_ROOT) for path in SOURCE_ROOT.rglob("*.py"))
    sources = sources_of(files)

    assert sources
    assert denied_numbers(sources) == []
