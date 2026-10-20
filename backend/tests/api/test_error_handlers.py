import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from car_advisor.agent.runner import AgentBusy
from car_advisor.api.app import create_app
from car_advisor.conversations.profile import FieldError, ProfileValidationError
from car_advisor.conversations.service import ConversationConflict, ConversationNotFound
from car_advisor.logs import JsonLinesFormatter

ROUTE = "/api/conversations/{conversation_id}/boom"
HEBREW = "יש להזין סכום שאינו שלילי"
DATABASE_DOWN = {"code": "database_unavailable"}


def client_that_raises(error: Exception) -> TestClient:
    app = create_app()

    @app.get(ROUTE)
    def boom(conversation_id: str) -> None:
        raise error

    return TestClient(app)


def lines(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    formatter = JsonLinesFormatter()
    parsed = [json.loads(formatter.format(record)) for record in caplog.records]
    return [line for line in parsed if line["event"] == event]


@pytest.mark.parametrize(
    ("error", "status", "body"),
    [
        pytest.param(
            ConversationNotFound(), 404, {"code": "conversation_not_found"}, id="not-found"
        ),
        pytest.param(AgentBusy(), 409, {"code": "agent_busy"}, id="agent-busy"),
        pytest.param(ConversationConflict(), 409, {"code": "conflict"}, id="conflict"),
        pytest.param(
            ProfileValidationError([FieldError("base_salary", HEBREW)]),
            422,
            {"code": "invalid_fields", "fields": [{"field": "base_salary", "message": HEBREW}]},
            id="invalid-fields",
        ),
        pytest.param(
            OperationalError("SELECT 1", {}, Exception("down")),
            503,
            DATABASE_DOWN,
            id="operational-error",
        ),
        pytest.param(
            InterfaceError("SELECT 1", {}, Exception("closed")),
            503,
            DATABASE_DOWN,
            id="interface-error",
        ),
        pytest.param(PoolTimeout("pool exhausted"), 503, DATABASE_DOWN, id="pool-timeout"),
    ],
)
def test_a_domain_error_gets_its_status_and_a_small_body(
    error: Exception, status: int, body: dict[str, Any]
) -> None:
    response = client_that_raises(error).get("/api/conversations/c-1/boom")

    assert (response.status_code, response.json()) == (status, body)


def test_a_handled_error_is_logged_with_its_status_and_the_route_template(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    client_that_raises(ConversationNotFound()).get("/api/conversations/c-1/boom")

    [line] = lines(caplog, "request")
    assert (line["status"], line["route"], line["conversation_id"]) == (404, ROUTE, "c-1")
    assert "error_type" not in line


def test_a_database_error_is_logged_with_its_type_and_never_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    error = OperationalError("SELECT salary FROM t WHERE x = :x", {"x": 25000}, Exception("25000"))

    client_that_raises(error).get("/api/conversations/c-1/boom")

    [line] = lines(caplog, "database_unavailable")
    assert line["error_type"] == "OperationalError"
    assert "25000" not in json.dumps([line, *lines(caplog, "request")])


def test_the_interactive_docs_pages_that_load_scripts_from_a_cdn_are_off() -> None:
    client = TestClient(create_app())

    assert [client.get(path).status_code for path in ("/docs", "/redoc")] == [404, 404]
