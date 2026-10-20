import pytest
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.dataset.models import UsageValueRow
from car_advisor.engine.types import CarKey, Powertrain

# Keys found by querying data/usage-values-2026.csv. Each row's fuel type and engine size agree
# with its electric column, which keeps the rows that look miscoded out of these checks.
ELECTRIC_MODELS = [
    pytest.param("2026-2023-1-01229-00177", "טסלה ארה''ב", "MODEL 3", id="tesla-model-3"),
    pytest.param("2026-2026-1-01417-00354", "טסלה גרמניה", "MODEL Y", id="tesla-model-y"),
    pytest.param("2026-2023-1-00481-00399", "יונדאי קוריאה", "IONIQ 5", id="hyundai-ioniq-5"),
    pytest.param("2026-2025-1-01014-00004", "בי ווי די", "ATTO 3", id="byd-atto-3"),
    pytest.param("2026-2026-1-00571-00054", "וולבו סין", "EX30 SM", id="volvo-ex30"),
    pytest.param("2026-2024-1-00724-00617", "פולקסווגן גרמנ", "ID.4", id="volkswagen-id-4"),
    pytest.param("2026-2025-1-00413-00488", "טויוטה יפן", "BZ4X", id="toyota-bz4x"),
    pytest.param("2026-2024-1-00885-00595", "קיה קוריאה", "NIRO EV", id="kia-niro-ev"),
]

# The file has no plug-in Corolla, so the plug-in row is a Prius.
COROLLA_AND_PRIUS = [
    pytest.param(
        "2026-2026-1-00839-00353",
        "טויוטה טורקיה",
        "COROLLA HYBRID",
        Powertrain.HYBRID,
        id="toyota-corolla-hybrid",
    ),
    pytest.param(
        "2026-2025-1-00430-00149",
        "טויוטה אנגליה",
        "COROLLA TS HSD",
        Powertrain.HYBRID,
        id="toyota-corolla-ts-hsd",
    ),
    pytest.param(
        "2026-2023-1-00413-00244",
        "טויוטה יפן",
        "PRIUS HYBRID",
        Powertrain.HYBRID,
        id="toyota-prius-hybrid",
    ),
    pytest.param(
        "2026-2021-1-00413-00174",
        "טויוטה יפן",
        "PRIUS PLUG IN",
        Powertrain.PLUG_IN,
        id="toyota-prius-plug-in",
    ),
]


def stored_car(sessions: sessionmaker[Session], key: str) -> UsageValueRow:
    with sessions() as session:
        row = session.get(UsageValueRow, CarKey.parse(key).model_dump())
    assert row is not None, f"{key} is not in the dataset"
    return row


@pytest.mark.parametrize(("key", "manufacturer_name", "trade_name"), ELECTRIC_MODELS)
def test_electric_models_are_electric(
    real_sessions: sessionmaker[Session], key: str, manufacturer_name: str, trade_name: str
) -> None:
    row = stored_car(real_sessions, key)

    assert (row.manufacturer_name, row.trade_name) == (manufacturer_name, trade_name)
    assert row.powertrain == Powertrain.ELECTRIC


@pytest.mark.parametrize(
    ("key", "manufacturer_name", "trade_name", "powertrain"), COROLLA_AND_PRIUS
)
def test_corolla_and_prius_hybrids_and_plug_ins_are_not_electric(
    real_sessions: sessionmaker[Session],
    key: str,
    manufacturer_name: str,
    trade_name: str,
    powertrain: Powertrain,
) -> None:
    row = stored_car(real_sessions, key)

    assert (row.manufacturer_name, row.trade_name) == (manufacturer_name, trade_name)
    assert row.powertrain == powertrain
