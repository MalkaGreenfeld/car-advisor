import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from car_advisor.agent.runner import AgentRunner
from car_advisor.api.app import create_app
from car_advisor.api.services import Services
from car_advisor.conversations.rep_questions import RepField, open_or_get
from car_advisor.engine import copy_he
from car_advisor.engine.config import CatalogCar
from car_advisor.logs import JsonLinesFormatter
from car_advisor.settings import Settings

CONVERSATIONS = "/api/conversations"
QUESTIONS = "/api/rep/questions"
UNKNOWN_ID = "6f0c1c2e-3b4a-4c5d-8e9f-0a1b2c3d4e5f"

# Values of the profile that Transport_Rep must never receive. None of them is a word or a number
# that a question text, an ID or a time could hold by chance.
SALARY = "7654321"
COMPONENT_LABEL = "Zebra commission"
COMPONENT_AMOUNT = "123457"
PRIVATE_PROFILE = {
    "display_name": "Dana",
    "eligibility_group": "C",
    "base_salary": SALARY,
    "pay_components": [
        {"kind": "commission", "label": COMPONENT_LABEL, "amount": COMPONENT_AMOUNT}
    ],
    "pension_rate_percent": "6.37",
    "study_fund_rate_percent": "2.41",
    "credit_points": "3.17",
}
PRIVATE_VALUES = (SALARY, COMPONENT_LABEL, COMPONENT_AMOUNT, "6.37", "2.41", "3.17")

VIEW_KEYS = {
    "id",
    "question_type",
    "question_text",
    "created_at",
    "employee",
    "eligibility_group",
    "car",
    "status",
    "answer",
}

PARTICIPATION = {"participation": {"amount": "350", "effective_date": "2026-01-01", "note": "Note"}}
TEXT = {"text": "The occupation allows the waiver"}


@dataclass
class RecordingRunner:
    """Stands in for AgentRunner and records the resumes that the routes schedule."""

    resumes: list[tuple[UUID, str]] = field(default_factory=list)
    # The state responses ask the runner whether the agent is available.
    agent_available: bool = False

    async def run_with_note(self, conversation_id: UUID, note: str) -> None:
        self.resumes.append((conversation_id, note))


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
def runner(app: FastAPI, services: Services) -> RecordingRunner:
    recorder = RecordingRunner()
    app.state.services = replace(services, runner=cast(AgentRunner, recorder))
    return recorder


@pytest.fixture
def conversation(client: TestClient) -> str:
    created = client.post(CONVERSATIONS).json()["id"]
    client.patch(f"{CONVERSATIONS}/{created}/profile", json={"changes": PRIVATE_PROFILE})
    return str(created)


@pytest.fixture
def car(services: Services) -> CatalogCar:
    return services.config.catalog.cars[0]


def ask(services: Services, conversation: str, field: RepField, car: CatalogCar | None) -> str:
    """Open a question the way the agent's tool does, and return its ID."""
    with services.sessions.begin() as session:
        question, _ = open_or_get(services.conversations, session, UUID(conversation), field, car)
        return str(question.id)


@pytest.fixture
def participation(services: Services, conversation: str, car: CatalogCar) -> str:
    return ask(services, conversation, RepField.NEW_CAR_PARTICIPATION, car)


@pytest.fixture
def free_text(services: Services, conversation: str) -> str:
    return ask(services, conversation, RepField.CURRENT_CAR, None)


def answer_url(question: str) -> str:
    return f"{QUESTIONS}/{question}/answer"


def listed(client: TestClient) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = client.get(QUESTIONS).json()["questions"]
    return questions


def logged(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """The lines of the app written so far, as the stdout formatter renders them."""
    formatter = JsonLinesFormatter()
    return [
        json.loads(formatter.format(record))
        for record in caplog.records
        if record.name.startswith("car_advisor")
    ]


def test_without_questions_the_list_is_empty(client: TestClient) -> None:
    response = client.get(QUESTIONS)

    assert (response.status_code, response.json()) == (200, {"questions": []})


def test_a_question_has_the_fields_of_req_12_3_and_nothing_else(
    client: TestClient, participation: str, car: CatalogCar
) -> None:
    [question] = listed(client)

    assert set(question) == VIEW_KEYS
    assert (question["id"], question["status"], question["answer"]) == (participation, "open", None)
    assert question["question_type"] == "participation"
    assert question["eligibility_group"] == "C"
    assert set(question["employee"]) == {"display_name", "number"}
    assert question["employee"]["display_name"] == "Dana"
    assert question["car"] == {"display_name": car.display_name, "car_key": str(car.official_key)}
    assert car.display_name in question["question_text"]
    assert question["created_at"].endswith("Z")


def test_the_rep_api_exposes_no_conversation_id_and_no_profile_value(
    client: TestClient, conversation: str, participation: str, free_text: str
) -> None:
    client.post(answer_url(free_text), json=TEXT)
    bodies = [
        client.get(QUESTIONS).text,
        client.post(answer_url(participation), json=PARTICIPATION).text,
        client.post(answer_url(participation), json=PARTICIPATION).text,
        client.get(QUESTIONS).text,
    ]

    everything = "\n".join(bodies)

    assert conversation not in everything
    assert conversation.replace("-", "") not in everything
    for private in PRIVATE_VALUES:
        assert private not in everything
    for name in ("conversation", "salary", "pension", "study_fund", "credit_points", "profile"):
        assert name not in everything


def test_open_questions_come_before_answered_ones_and_each_group_is_oldest_first(
    client: TestClient, services: Services, conversation: str, car: CatalogCar
) -> None:
    first = ask(services, conversation, RepField.CURRENT_CAR, None)
    second = ask(services, conversation, RepField.CURRENT_CAR_PARTICIPATION, None)
    third = ask(services, conversation, RepField.NEW_CAR_PARTICIPATION, car)
    client.post(answer_url(first), json=TEXT)

    questions = listed(client)

    assert [(q["id"], q["status"]) for q in questions] == [
        (second, "open"),
        (third, "open"),
        (first, "answered"),
    ]


def test_a_participation_answer_is_stored_and_the_conversation_resumes_once(
    client: TestClient, conversation: str, participation: str, runner: RecordingRunner
) -> None:
    response = client.post(answer_url(participation), json=PARTICIPATION)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == VIEW_KEYS
    assert (body["id"], body["status"]) == (participation, "answered")
    assert Decimal(body["answer"]["amount"]) == Decimal(350)
    assert (body["answer"]["effective_date"], body["answer"]["note"]) == ("2026-01-01", "Note")
    assert body["answer"]["text"] is None
    assert listed(client) == [body]
    [(resumed, note)] = runner.resumes
    assert str(resumed) == conversation
    assert RepField.NEW_CAR_PARTICIPATION.value in note
    state = client.get(f"{CONVERSATIONS}/{conversation}").json()
    assert state["open_questions"] == []


def test_the_note_of_a_participation_answer_is_optional(
    client: TestClient, participation: str, runner: RecordingRunner
) -> None:
    answer = {"participation": {"amount": "0", "effective_date": "2026-03-05"}}

    response = client.post(answer_url(participation), json=answer)

    assert response.status_code == 200
    assert Decimal(response.json()["answer"]["amount"]) == 0
    assert response.json()["answer"]["note"] is None
    assert len(runner.resumes) == 1


def test_a_text_answer_is_stored_for_any_other_question(
    client: TestClient, free_text: str, runner: RecordingRunner
) -> None:
    response = client.post(answer_url(free_text), json=TEXT)

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["answer"]["text"]) == ("answered", TEXT["text"])
    assert (body["answer"]["amount"], body["answer"]["effective_date"]) == (None, None)
    assert len(runner.resumes) == 1


def test_a_second_answer_gets_409_and_changes_nothing_and_resumes_nothing(
    client: TestClient, participation: str, runner: RecordingRunner
) -> None:
    first = client.post(answer_url(participation), json=PARTICIPATION)
    other = {"participation": {"amount": "999", "effective_date": "2027-02-02"}}

    second = client.post(answer_url(participation), json=other)

    assert (second.status_code, second.json()) == (409, {"code": "already_answered"})
    assert listed(client) == [first.json()]
    assert len(runner.resumes) == 1


def test_a_second_answer_of_the_other_kind_is_refused_the_same_way(
    client: TestClient, free_text: str, runner: RecordingRunner
) -> None:
    client.post(answer_url(free_text), json=TEXT)

    second = client.post(answer_url(free_text), json={"text": "Another answer"})

    assert (second.status_code, second.json()) == (409, {"code": "already_answered"})
    assert len(runner.resumes) == 1


def errors(*names: str) -> dict[str, Any]:
    fields = [{"field": n, "message": copy_he.REP_ANSWER_FIELD_ERRORS[n]} for n in names]
    return {"code": "invalid_answer", "fields": fields}


NO_FIELD = {"code": "invalid_answer"}
DATE = "2026-01-01"


ABSENT = object()


def participation_body(**changes: Any) -> dict[str, Any]:
    """A valid participation answer, with the given parts changed or (with ABSENT) left out."""
    parts = {"amount": "350", "effective_date": DATE} | changes
    return {"participation": {name: v for name, v in parts.items() if v is not ABSENT}}


P = "participation"
T = "free_text"


@pytest.mark.parametrize(
    ("kind", "body", "expected"),
    [
        pytest.param(P, participation_body(amount=ABSENT), ("amount",), id="no-amount"),
        pytest.param(P, participation_body(amount="-1"), ("amount",), id="negative-amount"),
        pytest.param(P, participation_body(amount="abc"), ("amount",), id="amount-not-a-number"),
        pytest.param(P, participation_body(amount=""), ("amount",), id="empty-amount"),
        pytest.param(P, participation_body(amount="12.345"), ("amount",), id="three-decimals"),
        pytest.param(P, participation_body(amount="1" * 11), ("amount",), id="too-many-digits"),
        pytest.param(
            P, participation_body(effective_date=ABSENT), ("effective_date",), id="no-date"
        ),
        pytest.param(
            P,
            participation_body(effective_date="2026-02-30"),
            ("effective_date",),
            id="no-such-day",
        ),
        pytest.param(
            P,
            participation_body(effective_date="05/03/2026"),
            ("effective_date",),
            id="day-first-date",
        ),
        pytest.param(
            P, participation_body(effective_date=""), ("effective_date",), id="empty-date"
        ),
        pytest.param(
            P,
            participation_body(amount="-5", effective_date="soon"),
            ("amount", "effective_date"),
            id="amount-and-date",
        ),
        pytest.param(P, participation_body(note="x" * 501), ("note",), id="long-note"),
        pytest.param(P, TEXT, ("amount", "effective_date"), id="text-for-a-participation"),
        pytest.param(T, {"text": ""}, ("text",), id="empty-text"),
        pytest.param(T, {"text": " \t\n "}, ("text",), id="blank-text"),
        pytest.param(T, {"text": "x" * 2001}, ("text",), id="long-text"),
        pytest.param(T, PARTICIPATION, ("text",), id="participation-for-a-text"),
    ],
)
def test_an_invalid_answer_gets_422_with_a_message_per_field_and_the_question_stays_open(
    request: pytest.FixtureRequest,
    client: TestClient,
    runner: RecordingRunner,
    kind: str,
    body: dict[str, Any],
    expected: tuple[str, ...],
) -> None:
    question = request.getfixturevalue(kind)

    response = client.post(answer_url(question), json=body)

    assert (response.status_code, response.json()) == (422, errors(*expected))
    [stored] = listed(client)
    assert (stored["status"], stored["answer"]) == ("open", None)
    assert runner.resumes == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="empty"),
        pytest.param({**PARTICIPATION, **TEXT}, id="both-shapes"),
        pytest.param({**PARTICIPATION, "txt": "x"}, id="unknown-field"),
        pytest.param({"participation": 350}, id="participation-not-an-object"),
        pytest.param({"participation": None, "text": None}, id="both-null"),
    ],
)
def test_a_body_of_the_wrong_shape_gets_422_without_a_field_to_blame(
    client: TestClient, free_text: str, runner: RecordingRunner, body: dict[str, Any]
) -> None:
    response = client.post(answer_url(free_text), json=body)

    assert (response.status_code, response.json()) == (422, NO_FIELD)
    assert listed(client)[0]["status"] == "open"
    assert runner.resumes == []


def test_a_body_that_is_not_an_object_gets_the_default_422(
    client: TestClient, free_text: str, runner: RecordingRunner
) -> None:
    response = client.post(answer_url(free_text), json=["text"])

    assert response.status_code == 422
    assert runner.resumes == []


@pytest.mark.parametrize("question", [UNKNOWN_ID, "not-a-uuid", "12345"])
def test_an_unknown_or_malformed_question_gets_404_and_resumes_nothing(
    client: TestClient, participation: str, runner: RecordingRunner, question: str
) -> None:
    response = client.post(answer_url(question), json=PARTICIPATION)

    assert response.status_code == 404
    assert "code" not in response.json()
    assert listed(client)[0]["status"] == "open"
    assert runner.resumes == []


def test_a_resume_that_cannot_run_does_not_fail_the_answer(
    client: TestClient, conversation: str, participation: str
) -> None:
    # The app of the fixtures has no API key, so the resume ends with a notice (Req 10.15, 12.8).
    response = client.post(answer_url(participation), json=PARTICIPATION)

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    messages = client.get(f"{CONVERSATIONS}/{conversation}").json()["messages"]
    assert [(m["role"], m["notice"]) for m in messages] == [("notice", "resume_failed")]


def test_the_log_has_the_route_and_the_status_and_none_of_the_answer(
    client: TestClient, participation: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    answer = {
        "participation": {
            "amount": "4455.50",
            "effective_date": DATE,
            "note": "Private note 987654",
        }
    }

    client.post(answer_url(participation), json=answer)
    client.post(answer_url(participation), json=answer)
    client.get(QUESTIONS)

    lines = [line for line in logged(caplog) if line["event"] == "request"]
    assert [(line["method"], line["route"], line["status"]) for line in lines] == [
        ("POST", "/api/rep/questions/{question_id}/answer", 200),
        ("POST", "/api/rep/questions/{question_id}/answer", 409),
        ("GET", "/api/rep/questions", 200),
    ]
    everything = json.dumps(logged(caplog), ensure_ascii=False)
    for private in ("4455", "Private note", "987654", SALARY, "Dana"):
        assert private not in everything
