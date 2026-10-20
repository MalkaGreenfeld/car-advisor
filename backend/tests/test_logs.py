import json
import logging
from collections.abc import Iterator
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import APIRouter, BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from car_advisor.logs import (
    JsonLinesFormatter,
    RequestLogMiddleware,
    configure_logging,
    log_agent_run,
    log_tool_call,
)

LOGGER = "car_advisor.logs"
WHITELIST = {
    "ts",
    "level",
    "event",
    "method",
    "route",
    "status",
    "duration_ms",
    "tool",
    "ok",
    "conversation_id",
    "stage",
    "error_type",
    "tax_year",
    "record_count",
    "fields",
}


def build_app() -> FastAPI:
    router = APIRouter(prefix="/api/conversations")

    @router.get("/{conversation_id}")
    def read(conversation_id: str) -> dict[str, str]:
        return {"id": conversation_id}

    @router.post("/{conversation_id}/resume")
    def resume(conversation_id: str, tasks: BackgroundTasks) -> None:
        tasks.add_task(logging.getLogger(LOGGER).info, "background_done")

    @router.get("/{conversation_id}/boom")
    def boom(conversation_id: str) -> None:
        raise RuntimeError("salary 25000")

    app = FastAPI()
    app.include_router(router)
    app.add_middleware(RequestLogMiddleware)
    return app


@pytest.fixture
def client(caplog: pytest.LogCaptureFixture) -> Iterator[TestClient]:
    caplog.set_level(logging.INFO, logger=LOGGER)
    with TestClient(build_app()) as test_client:
        yield test_client


@pytest.fixture
def root_logger() -> Iterator[logging.Logger]:
    root = logging.getLogger()
    level = root.level
    yield root
    for handler in root.handlers[:]:
        if isinstance(handler.formatter, JsonLinesFormatter):
            root.removeHandler(handler)
    root.setLevel(level)


def app_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    formatter = JsonLinesFormatter()
    return [formatter.format(record) for record in caplog.records if record.name == LOGGER]


def test_a_request_writes_one_line_with_the_route_template(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = client.get("/api/conversations/c-123", params={"salary": "25000"})

    assert response.status_code == 200
    [line] = app_lines(caplog)
    entry = json.loads(line)
    assert entry["event"] == "request"
    assert entry["level"] == "INFO"
    assert entry["method"] == "GET"
    assert entry["route"] == "/api/conversations/{conversation_id}"
    assert entry["status"] == 200
    assert entry["duration_ms"] >= 0
    assert entry["conversation_id"] == "c-123"
    assert datetime.fromisoformat(entry["ts"]).tzinfo is not None
    for raw in ("testserver", "/api/conversations/c-123", "salary", "25000"):
        assert raw not in line


def test_an_unmatched_path_is_logged_without_the_url(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = client.get("/api/private/25000", params={"q": "secret"})

    assert response.status_code == 404
    [line] = app_lines(caplog)
    entry = json.loads(line)
    assert entry["route"] is None
    assert entry["status"] == 404
    assert "conversation_id" not in entry
    for raw in ("/api/private", "25000", "secret"):
        assert raw not in line


def test_an_unhandled_exception_is_logged_as_500_and_reraised(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(RuntimeError):
        client.get("/api/conversations/c-1/boom")

    [line] = app_lines(caplog)
    entry = json.loads(line)
    assert entry["level"] == "ERROR"
    assert entry["route"] == "/api/conversations/{conversation_id}/boom"
    assert entry["status"] == 500
    assert entry["error_type"] == "RuntimeError"
    assert entry["conversation_id"] == "c-1"
    assert "25000" not in line


def test_background_tasks_run_after_the_request_line(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    client.post("/api/conversations/c-1/resume")

    events = [json.loads(line)["event"] for line in app_lines(caplog)]
    assert events == ["request", "background_done"]


def test_the_formatter_keeps_only_whitelisted_keys(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    fields = {key: "x" for key in WHITELIST - {"ts", "level", "event"}}

    try:
        raise ValueError("salary 25000")
    except ValueError:
        logging.getLogger(LOGGER).exception(
            "tool_call", extra={**fields, "salary": 25000, "api_key": "sk-test"}
        )

    [line] = app_lines(caplog)
    entry = json.loads(line)
    assert set(entry) == WHITELIST
    assert entry["event"] == "tool_call"
    assert entry["level"] == "ERROR"
    for raw in ("salary", "25000", "sk-test", "Traceback"):
        assert raw not in line


def test_a_tool_call_line_names_the_tool_the_outcome_the_duration_and_the_conversation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    conversation_id = uuid4()

    log_tool_call("lookup_car", ok=True, duration_ms=12.5, conversation_id=conversation_id)
    log_tool_call(
        "ask_rep",
        ok=False,
        duration_ms=3.0,
        conversation_id=str(conversation_id),
        error_type="OperationalError",
    )

    succeeded, failed = (json.loads(line) for line in app_lines(caplog))
    succeeded.pop("ts")
    failed.pop("ts")
    assert succeeded == {
        "level": "INFO",
        "event": "tool_call",
        "tool": "lookup_car",
        "ok": True,
        "duration_ms": 12.5,
        "conversation_id": str(conversation_id),
    }
    assert failed == {
        "level": "WARNING",
        "event": "tool_call",
        "tool": "ask_rep",
        "ok": False,
        "duration_ms": 3.0,
        "conversation_id": str(conversation_id),
        "error_type": "OperationalError",
    }


def test_an_agent_run_line_names_the_kind_the_outcome_the_duration_and_the_conversation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    conversation_id = uuid4()

    log_agent_run(
        "agent_turn",
        stage="replied",
        ok=True,
        duration_ms=1840.5,
        conversation_id=conversation_id,
    )
    log_agent_run(
        "agent_resume",
        stage="failed",
        ok=False,
        duration_ms=60001.2,
        conversation_id=str(conversation_id),
        error_type="UnexpectedModelBehavior",
    )

    replied, failed = (json.loads(line) for line in app_lines(caplog))
    replied.pop("ts")
    failed.pop("ts")
    assert replied == {
        "level": "INFO",
        "event": "agent_turn",
        "stage": "replied",
        "ok": True,
        "duration_ms": 1840.5,
        "conversation_id": str(conversation_id),
    }
    assert failed == {
        "level": "WARNING",
        "event": "agent_resume",
        "stage": "failed",
        "ok": False,
        "duration_ms": 60001.2,
        "conversation_id": str(conversation_id),
        "error_type": "UnexpectedModelBehavior",
    }


def test_configure_logging_writes_json_lines_to_stdout(
    root_logger: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    configure_logging()

    logging.getLogger(LOGGER).info("dataset_imported", extra={"record_count": 3, "salary": 1})

    assert sum(isinstance(h.formatter, JsonLinesFormatter) for h in root_logger.handlers) == 1
    [line] = capsys.readouterr().out.splitlines()
    entry = json.loads(line)
    entry.pop("ts")
    assert entry == {"level": "INFO", "event": "dataset_imported", "record_count": 3}
