import json
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Final, Literal
from uuid import UUID

from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_FIELD_KEYS: Final = (
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
)
_HANDLER_NAME: Final = "car_advisor.stdout"

logger = logging.getLogger(__name__)


class JsonLinesFormatter(logging.Formatter):
    """Render a record as one JSON line: `ts`, `level`, `event` and the whitelisted fields.

    Every other `extra` key and the traceback are dropped, so a value that the whitelist
    does not name, such as a salary amount, a document or a secret, cannot reach the logs.
    """

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        values = vars(record)
        line |= {key: values[key] for key in _FIELD_KEYS if key in values}
        return json.dumps(line, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Send INFO and above from every logger to stdout as JSON lines.

    Calling it again keeps the single handler, so building several apps in one process
    does not duplicate lines.
    """
    root = logging.getLogger()
    if not any(handler.name == _HANDLER_NAME for handler in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.name = _HANDLER_NAME
        handler.setFormatter(JsonLinesFormatter())
        root.addHandler(handler)
    root.setLevel(logging.INFO)


def log_tool_call(
    tool: str,
    *,
    ok: bool,
    duration_ms: float,
    conversation_id: UUID | str,
    error_type: str | None = None,
) -> None:
    """Write the `tool_call` line of one Agent_Tool invocation (Req 14.10).

    The line has the tool name, whether the call succeeded, how long it took and the conversation
    it belongs to, and on a failure the type of the exception. It takes no arguments, results or
    messages, because a tool call is full of salary data.
    """
    line: dict[str, object] = {
        "tool": tool,
        "ok": ok,
        "duration_ms": duration_ms,
        "conversation_id": str(conversation_id),
    }
    if error_type is not None:
        line["error_type"] = error_type
    logger.log(logging.INFO if ok else logging.WARNING, "tool_call", extra=line)


type AgentRunEvent = Literal["agent_turn", "agent_resume"]


def log_agent_run(
    event: AgentRunEvent,
    *,
    stage: str,
    ok: bool,
    duration_ms: float,
    conversation_id: UUID | str,
    error_type: str | None = None,
) -> None:
    """Write the line of one Advisor_Agent run: a chat turn or the resume after an answer.

    The event says which kind of run it was and `stage` how it ended, as a fixed word. The line
    takes no message, note, reply or exception message, because each of them can quote a salary.
    """
    line: dict[str, object] = {
        "stage": stage,
        "ok": ok,
        "duration_ms": duration_ms,
        "conversation_id": str(conversation_id),
    }
    if error_type is not None:
        line["error_type"] = error_type
    logger.log(logging.INFO if ok else logging.WARNING, event, extra=line)


class RequestLogMiddleware:
    """Write one `request` line per HTTP request, with the route template instead of the URL."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500
        written = False

        def write(status: int, error_type: str | None = None) -> None:
            nonlocal written
            written = True
            route = scope.get("route")
            line: dict[str, object] = {
                "method": scope["method"],
                "route": route.path if isinstance(route, Route) else None,
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            conversation_id = scope.get("path_params", {}).get("conversation_id")
            if conversation_id is not None:
                line["conversation_id"] = str(conversation_id)
            if error_type is not None:
                line["error_type"] = error_type
            level = logging.INFO if error_type is None else logging.ERROR
            logger.log(level, "request", extra=line)

        async def observed_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            # Background tasks run after the last body chunk; writing the line here keeps
            # them out of duration_ms and puts the line before their own log lines.
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                write(status)

        try:
            await self.app(scope, receive, observed_send)
        except Exception as exc:
            if not written:
                write(500, type(exc).__name__)
            raise
        if not written:
            write(status)
