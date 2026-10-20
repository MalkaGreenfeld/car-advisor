import json
import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import car_advisor.api.app as app_module
from car_advisor.api.app import create_app
from car_advisor.api.services import Services
from car_advisor.conversations.models import ConversationRow
from car_advisor.dataset.lookup import CarLookup
from car_advisor.engine import copy_he
from car_advisor.engine.types import CarAlternative, CarKey, WaiverAlternative
from car_advisor.logs import JsonLinesFormatter
from car_advisor.settings import Settings
from tests.agent.scripted_model import ScriptedModel, call, say
from tests.api.conftest import PASSWORD

CONVERSATIONS = "/api/conversations"
SEARCH = "/api/cars/search"
# The catalog car of the app_settings fixture, in group C, and the other car of its dataset.
TESLA = "2026-2020-1-01229-00001"
OTHER = "2026-2020-1-01229-00002"
UNKNOWN_ID = "6f0c1c2e-3b4a-4c5d-8e9f-0a1b2c3d4e5f"
API_KEY = "sk-test-Only-key-0000"

# The words of the employee and of the model carry no digit, so the number guard lets a reply
# through, and an amount that a log line must not hold cannot be one of them.
MESSAGE = "שלום, אני רוצה לבדוק את הרכב שלי"
REPLY = "שמרתי את הפרטים"
# An amount that no ID, time or duration in a log line can equal by chance.
SALARY = "7654321"

# The keys that the client in frontend/src/app/shared/api-models.ts reads.
STATE_KEYS = {"id", "messages", "profile", "alternatives", "comparison", "open_questions", "agent"}
PROFILE_KEYS = {
    "display_name",
    "eligibility_group",
    "base_salary",
    "pay_components",
    "pension_rate_percent",
    "study_fund_rate_percent",
    "credit_points",
    "occupation",
    "children_under_23",
    "has_current_car",
    "current_car",
    "current_car_key",
    "current_car_participation",
}
MESSAGE_KEYS = {"id", "role", "text", "notice", "created_at"}
TODAY_KEYS = {
    "status",
    "car",
    "participation",
    "usage_value",
    "ev_refund",
    "car_maintenance",
    "net_pay",
    "missing",
}
ALTERNATIVE_KEYS = {
    "position",
    "status",
    "car",
    "eligibility",
    "car_cost",
    "car_budget",
    "participation",
    "participation_effective_date",
    "employer_gap_share",
    "employee_gap_share",
    "usage_value",
    "ev_refund",
    "car_maintenance",
    "unused_budget",
    "net_pay",
    "difference",
    "breakdown",
    "explanation",
    "missing",
}
CANDIDATE_KEYS = {
    "car_key",
    "manufacturer_name",
    "trade_name",
    "model_code",
    "model_description",
    "vehicle_type_code",
    "engine_cc",
    "powertrain",
    "list_price",
}


@pytest.fixture
def app(app_settings: Settings, sqlite_startup: Path) -> FastAPI:
    return create_app(app_settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as started:
        yield started


@pytest.fixture
def services(app: FastAPI, client: TestClient) -> Services:
    # After the client, which runs the startup that builds them.
    services: Services = app.state.services
    return services


@pytest.fixture
def conversation(client: TestClient) -> str:
    return str(client.post(CONVERSATIONS).json()["id"])


def url(conversation: str, suffix: str = "") -> str:
    return f"{CONVERSATIONS}/{conversation}{suffix}"


def stored_conversations(services: Services) -> int:
    with services.sessions() as session:
        return session.execute(select(func.count()).select_from(ConversationRow)).scalar_one()


def show_cars(services: Services, conversation: str, *keys: str) -> None:
    """Set one car column per key. No route does it, because only the agent's tool sets columns."""
    alternatives = [CarAlternative(car_key=CarKey.parse(key)) for key in keys]
    services.conversations.set_alternatives(UUID(conversation), alternatives)


def logged(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """The lines of the app written so far, as the stdout formatter renders them."""
    formatter = JsonLinesFormatter()
    return [
        json.loads(formatter.format(record))
        for record in caplog.records
        if record.name.startswith("car_advisor")
    ]


def events(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    return [line for line in logged(caplog) if line["event"] == event]


def test_creating_a_conversation_gives_201_and_an_empty_state(client: TestClient) -> None:
    response = client.post(CONVERSATIONS)

    assert response.status_code == 201
    body = response.json()
    assert UUID(body["id"])
    assert (body["messages"], body["alternatives"], body["open_questions"]) == ([], [], [])
    assert body["comparison"] is None
    assert body["agent"] == {"available": False, "running": False}
    assert set(body["profile"].values()) == {None}


def test_a_new_conversation_keeps_the_previous_one_and_reading_gives_what_creating_gave(
    client: TestClient, conversation: str
) -> None:
    client.patch(url(conversation, "/profile"), json={"changes": {"base_salary": "16000"}})

    created = client.post(CONVERSATIONS).json()

    assert created["id"] != conversation
    assert client.get(url(created["id"])).json() == created
    assert client.get(url(conversation)).json()["profile"]["base_salary"] == "16000"


ROUTES = [
    pytest.param("GET", "", None, id="state"),
    pytest.param("POST", "/messages", {"text": MESSAGE}, id="message"),
    pytest.param("PATCH", "/profile", {"changes": {}}, id="profile"),
    pytest.param("PUT", "/alternatives/0", {"kind": "car", "car_key": TESLA}, id="alternative"),
    pytest.param("GET", "/catalog", None, id="catalog"),
]


@pytest.mark.parametrize("conversation_id", [UNKNOWN_ID, "not-a-uuid", "12345", UNKNOWN_ID[:-1]])
@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES)
def test_an_unknown_or_malformed_id_gives_404_and_creates_nothing(
    client: TestClient,
    services: Services,
    conversation: str,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
    conversation_id: str,
) -> None:
    response = client.request(method, url(conversation_id, suffix), json=body)

    assert (response.status_code, response.json()) == (404, {"code": "conversation_not_found"})
    assert stored_conversations(services) == 1


def test_without_an_api_key_a_message_is_kept_and_answered_with_the_unavailable_notice(
    client: TestClient, conversation: str
) -> None:
    response = client.post(url(conversation, "/messages"), json={"text": MESSAGE})

    assert response.status_code == 200
    body = response.json()
    assert [(m["role"], m["text"], m["notice"]) for m in body["messages"]] == [
        ("employee", MESSAGE, None),
        ("notice", None, "agent_unavailable"),
    ]
    assert body["agent"] == {"available": False, "running": False}
    assert client.get(url(conversation)).json() == body


def test_without_an_api_key_the_profile_and_the_alternatives_still_work(
    client: TestClient, services: Services, conversation: str
) -> None:
    client.post(url(conversation, "/messages"), json={"text": MESSAGE})
    show_cars(services, conversation, TESLA)

    profile = client.patch(
        url(conversation, "/profile"), json={"changes": {"base_salary": "16000"}}
    )
    alternative = client.put(
        url(conversation, "/alternatives/0"), json={"kind": "car", "car_key": OTHER}
    )

    assert (profile.status_code, alternative.status_code) == (200, 200)
    assert profile.json()["profile"]["base_salary"] == "16000"
    assert alternative.json()["alternatives"] == [{"kind": "car", "car_key": OTHER}]
    assert alternative.json()["profile"]["base_salary"] == "16000"


def test_a_message_while_a_run_holds_the_guard_gives_409_and_is_not_stored(
    client: TestClient, services: Services, conversation: str
) -> None:
    guard = services.conversations.try_start_run(UUID(conversation))
    assert guard is not None

    refused = client.post(url(conversation, "/messages"), json={"text": MESSAGE})
    state = client.get(url(conversation)).json()
    services.conversations.finish_run(UUID(conversation), guard)
    accepted = client.post(url(conversation, "/messages"), json={"text": MESSAGE})

    assert (refused.status_code, refused.json()) == (409, {"code": "agent_busy"})
    assert (state["messages"], state["agent"]["running"]) == ([], True)
    assert accepted.status_code == 200
    assert len(accepted.json()["messages"]) == 2


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"text": ""}, id="empty"),
        pytest.param({"text": " \t\n"}, id="blank"),
        pytest.param({"text": "x" * 2001}, id="2001-characters"),
        pytest.param({}, id="no-text"),
        pytest.param({"text": MESSAGE, "txt": "oops"}, id="misspelled-field"),
    ],
)
def test_a_message_that_fails_the_request_model_gives_422_and_is_not_stored(
    client: TestClient, conversation: str, body: dict[str, Any]
) -> None:
    response = client.post(url(conversation, "/messages"), json=body)

    assert response.status_code == 422
    assert "detail" in response.json()
    assert client.get(url(conversation)).json()["messages"] == []


def test_a_message_of_2000_characters_is_accepted(client: TestClient, conversation: str) -> None:
    response = client.post(url(conversation, "/messages"), json={"text": "x" * 2000})

    assert response.status_code == 200


def test_a_valid_profile_change_is_stored_recalculated_and_shown_after_a_refresh(
    client: TestClient, conversation: str
) -> None:
    changes = {"eligibility_group": "C", "base_salary": "16000.50", "children_under_23": 2}

    response = client.patch(url(conversation, "/profile"), json={"changes": changes})

    assert response.status_code == 200
    body = response.json()
    assert (body["profile"]["eligibility_group"], body["profile"]["base_salary"]) == (
        "C",
        "16000.50",
    )
    assert body["profile"]["children_under_23"] == 2
    assert body["comparison"] is not None
    assert client.get(url(conversation)).json() == body


def test_a_null_clears_a_profile_field(client: TestClient, conversation: str) -> None:
    client.patch(url(conversation, "/profile"), json={"changes": {"children_under_23": 2}})

    response = client.patch(
        url(conversation, "/profile"), json={"changes": {"children_under_23": None}}
    )

    assert response.status_code == 200
    assert response.json()["profile"]["children_under_23"] is None


def test_an_invalid_profile_change_gives_422_with_a_message_per_field_and_stores_nothing(
    client: TestClient, conversation: str
) -> None:
    client.patch(url(conversation, "/profile"), json={"changes": {"base_salary": "16000"}})
    before = client.get(url(conversation)).json()
    changes = {
        "eligibility_group": "E",
        "base_salary": "-1",
        "pension_rate_percent": "101",
        "credit_points": "abc",
        "children_under_23": 2.5,
        "no_such_field": 1,
        # Valid, and still not stored, because the change is all or nothing (Req 11.3).
        "occupation": "Driver",
    }

    response = client.patch(url(conversation, "/profile"), json={"changes": changes})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_fields"
    assert {error["field"]: error["message"] for error in body["fields"]} == {
        "eligibility_group": copy_he.PROFILE_FIELD_RANGES["eligibility_group"],
        "base_salary": copy_he.PROFILE_FIELD_RANGES["base_salary"],
        "pension_rate_percent": copy_he.PROFILE_FIELD_RANGES["pension_rate_percent"],
        "credit_points": copy_he.PROFILE_FIELD_RANGES["credit_points"],
        "children_under_23": copy_he.PROFILE_FIELD_RANGES["children_under_23"],
        "no_such_field": copy_he.UNKNOWN_PROFILE_FIELD,
    }
    assert client.get(url(conversation)).json() == before


@pytest.mark.parametrize("body", [{}, {"changes": [["base_salary", "1"]]}, {"changes": {}, "x": 1}])
def test_a_profile_body_of_the_wrong_shape_gives_422(
    client: TestClient, conversation: str, body: dict[str, Any]
) -> None:
    response = client.patch(url(conversation, "/profile"), json=body)

    assert response.status_code == 422
    assert "detail" in response.json()


def test_a_car_replaces_the_car_of_a_column_and_the_comparison_follows(
    client: TestClient, services: Services, conversation: str
) -> None:
    services.conversations.set_alternatives(
        UUID(conversation), [CarAlternative(car_key=CarKey.parse(TESLA)), WaiverAlternative()]
    )

    response = client.put(
        url(conversation, "/alternatives/0"), json={"kind": "car", "car_key": OTHER}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["alternatives"] == [{"kind": "car", "car_key": OTHER}, {"kind": "waiver"}]
    first, second = body["comparison"]["alternatives"]
    assert (first["position"], first["car"]["car_key"]) == (0, OTHER)
    assert (second["position"], second["car"]) == (1, None)
    assert client.get(url(conversation)).json() == body


@pytest.mark.parametrize("position", [1, 5, -1])
def test_a_position_without_a_column_gives_404_and_changes_nothing(
    client: TestClient, services: Services, conversation: str, position: int
) -> None:
    show_cars(services, conversation, TESLA)
    before = client.get(url(conversation)).json()

    response = client.put(
        url(conversation, f"/alternatives/{position}"), json={"kind": "car", "car_key": OTHER}
    )

    assert response.status_code == 404
    assert "code" not in response.json()
    assert client.get(url(conversation)).json() == before


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"kind": "waiver"}, id="waiver"),
        pytest.param({"kind": "car"}, id="no-key"),
        pytest.param({"kind": "car", "car_key": "2026-2020-1-01229"}, id="short-key"),
        pytest.param({"kind": "car", "car_key": OTHER, "position": 0}, id="extra-field"),
    ],
)
def test_an_alternative_body_that_fails_the_request_model_gives_422(
    client: TestClient, services: Services, conversation: str, body: dict[str, Any]
) -> None:
    show_cars(services, conversation, TESLA)

    response = client.put(url(conversation, "/alternatives/0"), json=body)

    assert response.status_code == 422
    assert "detail" in response.json()
    assert client.get(url(conversation)).json()["alternatives"] == [
        {"kind": "car", "car_key": TESLA}
    ]


def test_a_position_that_is_not_a_number_gives_422(client: TestClient, conversation: str) -> None:
    response = client.put(
        url(conversation, "/alternatives/first"), json={"kind": "car", "car_key": OTHER}
    )

    assert response.status_code == 422


TESLA_IN_CATALOG = {"car_key": TESLA, "display_name": "Tesla Model 3", "group": "C"}


@pytest.mark.parametrize(
    ("group", "cars"),
    [("A", []), ("B", []), ("C", [TESLA_IN_CATALOG]), ("D", [TESLA_IN_CATALOG])],
)
def test_the_catalog_has_the_cars_of_the_group_and_of_the_groups_below_it(
    client: TestClient, conversation: str, group: str, cars: list[dict[str, str]]
) -> None:
    client.patch(url(conversation, "/profile"), json={"changes": {"eligibility_group": group}})

    response = client.get(url(conversation, "/catalog"))

    assert (response.status_code, response.json()) == (200, {"cars": cars, "missing_field": None})


def test_the_catalog_without_a_stored_group_is_empty_and_names_the_missing_field(
    client: TestClient, conversation: str
) -> None:
    response = client.get(url(conversation, "/catalog"))

    assert (response.status_code, response.json()) == (
        200,
        {"cars": [], "missing_field": "eligibility_group"},
    )


def test_the_catalog_is_empty_again_once_the_group_is_cleared(
    client: TestClient, conversation: str
) -> None:
    client.patch(url(conversation, "/profile"), json={"changes": {"eligibility_group": "D"}})
    client.patch(url(conversation, "/profile"), json={"changes": {"eligibility_group": None}})

    body = client.get(url(conversation, "/catalog")).json()

    assert body == {"cars": [], "missing_field": "eligibility_group"}


def test_a_search_gives_every_matching_car_and_asks_for_a_selection(client: TestClient) -> None:
    response = client.get(SEARCH, params={"registration_year": "2020", "text": " model 3 "})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"candidates", "selection_required", "none_found"}
    assert (body["selection_required"], body["none_found"]) == (True, False)
    assert [candidate["car_key"] for candidate in body["candidates"]] == [TESLA, OTHER]
    first = body["candidates"][0]
    assert set(first) == CANDIDATE_KEYS
    assert (first["trade_name"], first["model_code"], first["vehicle_type_code"]) == (
        "MODEL 3",
        "00001",
        "1",
    )


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"registration_year": "2019", "text": "model"}, id="other-year"),
        pytest.param({"registration_year": "2020", "text": "no such car"}, id="other-name"),
    ],
)
def test_a_search_without_a_match_says_so_and_is_not_an_error(
    client: TestClient, params: dict[str, str]
) -> None:
    response = client.get(SEARCH, params=params)

    assert (response.status_code, response.json()) == (
        200,
        {"candidates": [], "selection_required": False, "none_found": True},
    )


def requirement(*names: str) -> dict[str, Any]:
    fields = [{"field": name, "message": copy_he.SEARCH_INPUT_REQUIRED[name]} for name in names]
    return {"code": "missing_inputs", "fields": fields}


@pytest.mark.parametrize(
    ("params", "missing"),
    [
        pytest.param({}, ("registration_year", "text"), id="nothing"),
        pytest.param({"registration_year": "2020"}, ("text",), id="no-text"),
        pytest.param({"registration_year": "2020", "text": ""}, ("text",), id="empty-text"),
        pytest.param({"registration_year": "2020", "text": " \t "}, ("text",), id="blank-text"),
        pytest.param({"text": "model"}, ("registration_year",), id="no-year"),
    ],
)
def test_a_search_that_lacks_an_input_gives_422_and_names_what_is_missing(
    client: TestClient, params: dict[str, str], missing: tuple[str, ...]
) -> None:
    response = client.get(SEARCH, params=params)

    assert (response.status_code, response.json()) == (422, requirement(*missing))


@pytest.mark.parametrize("year", ["", " ", "abc", "202", "20201", "2020.5", "9" * 30])
def test_a_year_that_is_blank_or_not_four_digits_counts_as_missing(
    client: TestClient, year: str
) -> None:
    response = client.get(SEARCH, params={"registration_year": year, "text": "model"})

    assert (response.status_code, response.json()) == (422, requirement("registration_year"))


def test_a_search_that_cannot_read_the_dataset_gives_503_and_logs_no_message(
    app: FastAPI, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    def refuse() -> NoReturn:
        raise sqlite3.OperationalError("Server=db.invalid;Password=hunter2")

    engine = create_engine("sqlite+pysqlite://", creator=refuse, poolclass=NullPool)
    unreadable = CarLookup(sessionmaker(engine), 2026)
    app.state.services = replace(app.state.services, lookup=unreadable)

    response = client.get(SEARCH, params={"registration_year": "2020", "text": "model"})

    assert (response.status_code, response.json()) == (503, {"code": "lookup_failed"})
    [failure] = events(caplog, "lookup_failed")
    assert (failure["stage"], failure["error_type"]) == ("car_search", "LookupFailed")
    assert "hunter2" not in json.dumps(logged(caplog))


def test_a_search_that_raises_gives_503_and_logs_the_error_type_and_never_its_message(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    def raises(self: CarLookup, registration_year: int | None, text: str | None) -> NoReturn:
        raise OperationalError("SELECT 1", {"text": "hunter2"}, Exception("hunter2"))

    monkeypatch.setattr(CarLookup, "search", raises)

    response = client.get(SEARCH, params={"registration_year": "2020", "text": "model"})

    assert (response.status_code, response.json()) == (503, {"code": "lookup_failed"})
    [failure] = events(caplog, "lookup_failed")
    assert failure["error_type"] == "OperationalError"
    assert "hunter2" not in json.dumps(logged(caplog))


def test_the_app_builds_an_openapi_schema_with_the_employee_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert {
        "/api/conversations",
        "/api/conversations/{conversation_id}",
        "/api/conversations/{conversation_id}/messages",
        "/api/conversations/{conversation_id}/profile",
        "/api/conversations/{conversation_id}/alternatives/{position}",
        "/api/conversations/{conversation_id}/catalog",
        "/api/cars/search",
    } <= set(paths)


def test_the_state_has_exactly_the_keys_the_client_reads(
    client: TestClient, services: Services, conversation: str
) -> None:
    client.post(url(conversation, "/messages"), json={"text": MESSAGE})
    services.conversations.set_alternatives(
        UUID(conversation), [CarAlternative(car_key=CarKey.parse(TESLA)), WaiverAlternative()]
    )

    state = client.patch(
        url(conversation, "/profile"), json={"changes": {"eligibility_group": "C"}}
    ).json()

    assert set(state) == STATE_KEYS
    assert set(state["profile"]) == PROFILE_KEYS
    assert set(state["agent"]) == {"available", "running"}
    assert [set(message) for message in state["messages"]] == [MESSAGE_KEYS, MESSAGE_KEYS]
    assert state["messages"][0]["created_at"].endswith("Z")
    assert set(state["comparison"]) == {"today", "alternatives"}
    assert set(state["comparison"]["today"]) == TODAY_KEYS
    assert [set(column) for column in state["comparison"]["alternatives"]] == [ALTERNATIVE_KEYS] * 2


def app_with_model(monkeypatch: pytest.MonkeyPatch, settings: Settings, model: Model) -> FastAPI:
    """The app with an API key in its settings and the given model in place of the provider's."""
    monkeypatch.setattr(app_module, "build_model", lambda settings, api_key: model)
    return create_app(settings.model_copy(update={"openai_api_key": SecretStr(API_KEY)}))


def test_a_turn_writes_a_line_per_request_and_per_tool_call_and_nothing_private(
    monkeypatch: pytest.MonkeyPatch,
    app_settings: Settings,
    sqlite_startup: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = ScriptedModel(call("update_profile", changes={"base_salary": SALARY}), say(REPLY))
    caplog.set_level(logging.INFO)

    with TestClient(app_with_model(monkeypatch, app_settings, script.model)) as client:
        conversation = str(client.post(CONVERSATIONS).json()["id"])
        turn = client.post(url(conversation, "/messages"), json={"text": f"{MESSAGE} {SALARY}"})
        client.patch(url(conversation, "/profile"), json={"changes": {"base_salary": SALARY}})
        client.get(url(conversation))

    assert turn.status_code == 200
    assert turn.json()["agent"] == {"available": True, "running": False}
    assert [message["text"] for message in turn.json()["messages"]] == [
        f"{MESSAGE} {SALARY}",
        REPLY,
    ]
    template = "/api/conversations/{conversation_id}"
    lines = events(caplog, "request")
    # The route is the template and never the URL. Each router carries the /api prefix itself, so
    # the template is the whole path that a client calls.
    assert [(line["method"], line["route"], line["status"]) for line in lines] == [
        ("POST", "/api/conversations", 201),
        ("POST", f"{template}/messages", 200),
        ("PATCH", f"{template}/profile", 200),
        ("GET", template, 200),
    ]
    assert [line.get("conversation_id") for line in lines] == [None, *[conversation] * 3]
    for line in lines:
        assert {"ts", "level", "event", "method", "route", "status", "duration_ms"} <= set(line)
        assert isinstance(line["duration_ms"], int | float)

    [tool] = events(caplog, "tool_call")
    assert (tool["tool"], tool["ok"], tool["conversation_id"]) == (
        "update_profile",
        True,
        conversation,
    )
    assert {"ts", "level", "event", "duration_ms"} <= set(tool)

    everything = json.dumps(logged(caplog), ensure_ascii=False)
    for private in (SALARY, MESSAGE, REPLY, API_KEY, PASSWORD):
        assert private not in everything


def test_a_profile_change_and_a_car_change_do_not_invoke_the_model(
    monkeypatch: pytest.MonkeyPatch, app_settings: Settings, sqlite_startup: Path
) -> None:
    script = ScriptedModel()
    app = app_with_model(monkeypatch, app_settings, script.model)

    with TestClient(app) as client:
        conversation = str(client.post(CONVERSATIONS).json()["id"])
        show_cars(app.state.services, conversation, TESLA)
        profile = client.patch(
            url(conversation, "/profile"), json={"changes": {"base_salary": "1"}}
        )
        car = client.put(
            url(conversation, "/alternatives/0"), json={"kind": "car", "car_key": OTHER}
        )

    assert (profile.status_code, car.status_code) == (200, 200)
    assert script.requests == []
    assert car.json()["messages"] == []


def test_a_model_that_fails_leaves_the_message_and_a_notice_and_still_gives_200(
    monkeypatch: pytest.MonkeyPatch, app_settings: Settings, sqlite_startup: Path
) -> None:
    def fail(messages: list[ModelMessage], info: AgentInfo) -> NoReturn:
        raise RuntimeError("the model is down")

    with TestClient(app_with_model(monkeypatch, app_settings, FunctionModel(fail))) as client:
        conversation = str(client.post(CONVERSATIONS).json()["id"])
        response = client.post(url(conversation, "/messages"), json={"text": MESSAGE})

    assert response.status_code == 200
    assert [(m["role"], m["text"], m["notice"]) for m in response.json()["messages"]] == [
        ("employee", MESSAGE, None),
        ("notice", None, "agent_failed"),
    ]
