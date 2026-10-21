import asyncio
import itertools
import json
import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import NoReturn
from uuid import UUID

import pytest
from pydantic_ai import ModelHTTPError, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.exc import OperationalError

import car_advisor.agent.runner as runner_module
from car_advisor.agent.advisor import INSTRUCTIONS, build_advisor
from car_advisor.agent.runner import AgentBusy, AgentRunner, NoticeCode, answer_note
from car_advisor.agent.tools import AdvisorDeps, RepSent, ask_rep
from car_advisor.conversations.rep_questions import (
    Answered,
    ParticipationAnswer,
    RepField,
    TextAnswer,
    answer,
)
from car_advisor.conversations.service import (
    ConversationNotFound,
    ConversationService,
    MessageRole,
    NewMessage,
)
from car_advisor.dataset.lookup import CarLookup
from car_advisor.engine import copy_he
from car_advisor.engine.types import CarKey, MissingField
from car_advisor.logs import JsonLinesFormatter
from car_advisor.rules.loader import AppConfig

ATTO_3_KEY = CarKey.parse("2026-2025-1-01014-00003")  # group C, "BYD Atto 3" in the catalog

# The words of the people and of the model. None has a digit, so the number guard lets every reply
# through, and none may appear in a log line.
MESSAGE = "שלום, אני רוצה לבדוק את הרכב שלי"
AGAIN = "ננסה שוב"
REPLY = "שלום, אשמח לעזור"
SECOND_REPLY = "בשמחה, הנה ההמשך"
NOTE = "Note from the application, not from the employee: the representative answered."
# A figure that no ID, time or duration in a log line can equal by chance.
SALARY = 7654321

TranscriptItem = tuple[MessageRole, str | None, NoticeCode | None]


def say(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def call(tool: str, **arguments: object) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, arguments)])


@dataclass(frozen=True)
class Seen:
    messages: list[ModelMessage]
    instructions: str | None


class Script:
    """A model function that plays the given responses in turn and keeps what each request had."""

    def __init__(self, responses: Iterable[ModelResponse]) -> None:
        self._responses = iter(responses)
        self.requests: list[Seen] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append(Seen(list(messages), info.instructions))
        return next(self._responses)

    @property
    def model(self) -> FunctionModel:
        return FunctionModel(self)


def replying(text: str = REPLY) -> Script:
    return Script(itertools.repeat(say(text)))


def raising(error: Exception) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> NoReturn:
        raise error

    return FunctionModel(respond)


def never_answering() -> FunctionModel:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(30)
        return say(REPLY)

    return FunctionModel(respond)


@pytest.fixture
def make_runner(
    service: ConversationService, lookup: CarLookup, app_config: AppConfig
) -> Callable[[Model | None], AgentRunner]:
    def make(model: Model | None) -> AgentRunner:
        advisor = None if model is None else build_advisor(model)
        return AgentRunner(advisor, service, lookup, app_config)

    return make


@pytest.fixture
def conversation_id(service: ConversationService) -> UUID:
    return service.create()


@pytest.fixture
def run_lines(caplog: pytest.LogCaptureFixture) -> Callable[[], list[dict[str, object]]]:
    """The lines about runs written so far, as the stdout formatter renders them."""
    caplog.set_level(logging.INFO)

    def lines() -> list[dict[str, object]]:
        formatter = JsonLinesFormatter()
        parsed = [json.loads(formatter.format(record)) for record in caplog.records]
        return [line for line in parsed if str(line["event"]).startswith("agent_")]

    return lines


def turn(runner: AgentRunner, conversation_id: UUID, text: str) -> None:
    asyncio.run(runner.run_turn(conversation_id, text))


def resume(
    runner: AgentRunner, conversation_id: UUID, failure_notice: NoticeCode | None = None
) -> None:
    """Resume on NOTE, with the failure notice of the runner or the one that is asked for."""
    if failure_notice is None:
        asyncio.run(runner.run_with_note(conversation_id, NOTE))
    else:
        asyncio.run(runner.run_with_note(conversation_id, NOTE, failure_notice))


def prompts(request: Seen) -> list[str]:
    """The user prompts of a request, which are the messages and notes the agent was given."""
    return [
        part.content
        for message in request.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    ]


def transcript(service: ConversationService, conversation_id: UUID) -> list[TranscriptItem]:
    return [
        (message.role, message.text, message.notice)
        for message in service.state(conversation_id).messages
    ]


def guard_is_free(service: ConversationService, conversation_id: UUID) -> bool:
    return not service.state(conversation_id).agent.running


def test_a_turn_stores_the_message_then_the_reply_and_the_history(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    seen: list[tuple[bool, list[TranscriptItem]]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(
            (service.state(conversation_id).agent.running, transcript(service, conversation_id))
        )
        return say(REPLY)

    assert service.load_history(conversation_id) is None

    turn(make_runner(FunctionModel(respond)), conversation_id, MESSAGE)

    # While the agent works, the guard is held and the message of the employee is stored.
    assert seen == [(True, [(MessageRole.EMPLOYEE, MESSAGE, None)])]
    assert transcript(service, conversation_id) == [
        (MessageRole.EMPLOYEE, MESSAGE, None),
        (MessageRole.ADVISOR, REPLY, None),
    ]
    history = service.load_history(conversation_id)
    assert history is not None
    assert REPLY in history.decode()
    assert guard_is_free(service, conversation_id)


def test_a_held_guard_raises_agent_busy_and_stores_nothing(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    run_lines: Callable[[], list[dict[str, object]]],
) -> None:
    script = replying()
    held_at = service.try_start_run(conversation_id)
    assert held_at is not None

    with pytest.raises(AgentBusy):
        turn(make_runner(script.model), conversation_id, MESSAGE)

    assert transcript(service, conversation_id) == []
    assert service.load_history(conversation_id) is None
    assert script.requests == []
    # The guard still belongs to the run that holds it.
    assert service.try_start_run(conversation_id) is None
    service.finish_run(conversation_id, held_at)
    assert run_lines() == []


def test_an_unknown_conversation_raises_not_found(
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    with pytest.raises(ConversationNotFound):
        turn(make_runner(replying().model), UUID(int=1), MESSAGE)


def test_the_agent_is_available_exactly_when_there_is_one(
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    assert make_runner(replying().model).agent_available is True
    assert make_runner(None).agent_available is False


def test_runs_use_the_event_loop_of_the_caller(
    conversation_id: UUID, make_runner: Callable[[Model | None], AgentRunner]
) -> None:
    # The HTTP client of a model provider belongs to the loop that first used it, so a loop of the
    # runner's own would make every second run fail.
    loops: list[asyncio.AbstractEventLoop] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        loops.append(asyncio.get_running_loop())
        return say(REPLY)

    async def two_runs() -> asyncio.AbstractEventLoop:
        runner = make_runner(FunctionModel(respond))
        await runner.run_turn(conversation_id, MESSAGE)
        await runner.run_with_note(conversation_id, NOTE)
        return asyncio.get_running_loop()

    caller = asyncio.run(two_runs())

    assert loops == [caller, caller]


def test_the_stored_history_holds_no_instructions_and_a_second_turn_replays_it(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    script = Script(
        [
            call("update_profile", changes={"occupation": "מנהל/ת מוצר", "base_salary": 14200}),
            call("compare_alternatives", alternatives=[{"kind": "waiver"}]),
            say(REPLY),
            say(SECOND_REPLY),
        ]
    )
    runner = make_runner(script.model)

    turn(runner, conversation_id, MESSAGE)

    history = service.load_history(conversation_id)
    assert history is not None
    stored = ModelMessagesTypeAdapter.validate_json(history)
    requests = [message for message in json.loads(history) if message["kind"] == "request"]
    # Every request of the run carried the instructions, but none of them is stored.
    assert all(request.instructions for request in script.requests[:3])
    assert [request["instructions"] for request in requests] == [None] * len(requests)
    assert INSTRUCTIONS[:60] not in history.decode()
    assert "Stored profile" not in history.decode()

    turn(runner, conversation_id, AGAIN)

    # The second run replays the whole stored history, tool results included, and gets the
    # instructions anew.
    replayed, instructions = script.requests[3].messages, script.requests[3].instructions
    assert len(replayed) == len(stored) + 1
    assert instructions is not None and instructions.startswith(INSTRUCTIONS)
    assert transcript(service, conversation_id) == [
        (MessageRole.EMPLOYEE, MESSAGE, None),
        (MessageRole.ADVISOR, REPLY, None),
        (MessageRole.EMPLOYEE, AGAIN, None),
        (MessageRole.ADVISOR, SECOND_REPLY, None),
    ]
    assert service.state(conversation_id).profile.base_salary == Decimal(14200)


def invalid_key() -> Model:
    return raising(ModelHTTPError(401, "gpt-6-luna", {"error": f"key {SALARY}"}))


def server_error() -> Model:
    return raising(ModelHTTPError(500, "gpt-6-luna", {"error": f"salary {SALARY}"}))


def broken_model() -> Model:
    return raising(RuntimeError(f"salary {SALARY}"))


def reply_with_an_unknown_number() -> Model:
    # The guard sends it back twice, and the run fails at the third try.
    return replying("₪52,000").model


def blank_reply() -> Model:
    return replying("   ").model


def assert_the_turn_fails_with(
    notice: NoticeCode,
    model: Model | None,
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    turn(make_runner(replying().model), conversation_id, MESSAGE)
    before = transcript(service, conversation_id)
    history = service.load_history(conversation_id)
    assert history is not None

    turn(make_runner(model), conversation_id, AGAIN)

    # The message stays, the notice follows it, and the history is the one from before.
    assert transcript(service, conversation_id) == [
        *before,
        (MessageRole.EMPLOYEE, AGAIN, None),
        (MessageRole.NOTICE, None, notice),
    ]
    assert service.load_history(conversation_id) == history
    assert guard_is_free(service, conversation_id)


@pytest.mark.parametrize(
    ("model", "notice"),
    [
        pytest.param(invalid_key, NoticeCode.AGENT_UNAVAILABLE, id="invalid-key"),
        pytest.param(server_error, NoticeCode.AGENT_FAILED, id="server-error"),
        pytest.param(broken_model, NoticeCode.AGENT_FAILED, id="other-error"),
        pytest.param(reply_with_an_unknown_number, NoticeCode.AGENT_FAILED, id="number-guard"),
        pytest.param(blank_reply, NoticeCode.AGENT_FAILED, id="blank-reply"),
    ],
)
def test_a_failed_turn_stores_its_notice_and_leaves_the_history(
    model: Callable[[], Model],
    notice: NoticeCode,
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    assert_the_turn_fails_with(notice, model(), service, conversation_id, make_runner)


def test_a_turn_without_an_agent_stores_the_message_and_the_unavailable_notice(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    assert_the_turn_fails_with(
        NoticeCode.AGENT_UNAVAILABLE, None, service, conversation_id, make_runner
    )


def test_a_turn_that_takes_too_long_stores_agent_failed_and_leaves_the_history(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_module, "RUN_TIMEOUT", 0.5)

    assert_the_turn_fails_with(
        NoticeCode.AGENT_FAILED, never_answering(), service, conversation_id, make_runner
    )


def test_a_turn_stuck_in_a_sync_tool_ends_on_time_and_the_tool_finishes_on_its_own(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Long enough for the run to reach the tool, which the test needs.
    monkeypatch.setattr(runner_module, "RUN_TIMEOUT", 1.0)
    release = threading.Event()
    finished = threading.Event()

    def stuck(conversation: UUID, changes: Mapping[str, object]) -> NoReturn:
        try:
            release.wait(10)
            raise RuntimeError("the tool finished late")
        finally:
            finished.set()

    monkeypatch.setattr(service, "update_profile_partial", stuck)
    script = Script([call("update_profile", changes={"base_salary": 14200}), say(REPLY)])

    async def scenario() -> None:
        try:
            await make_runner(script.model).run_turn(conversation_id, MESSAGE)
            # The run is over, and its tool still holds a worker thread.
            assert not finished.is_set()
            assert guard_is_free(service, conversation_id)
        finally:
            release.set()
        await asyncio.to_thread(finished.wait, 5)

    asyncio.run(scenario())

    assert finished.is_set()
    assert transcript(service, conversation_id) == [
        (MessageRole.EMPLOYEE, MESSAGE, None),
        (MessageRole.NOTICE, None, NoticeCode.AGENT_FAILED),
    ]
    assert service.load_history(conversation_id) is None


def test_a_first_turn_that_fails_leaves_no_history(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    turn(make_runner(broken_model()), conversation_id, MESSAGE)

    assert service.load_history(conversation_id) is None
    assert transcript(service, conversation_id) == [
        (MessageRole.EMPLOYEE, MESSAGE, None),
        (MessageRole.NOTICE, None, NoticeCode.AGENT_FAILED),
    ]


def test_a_database_error_while_storing_the_message_propagates_and_releases_the_guard(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = replying()

    def refuse(conversation: UUID, message: NewMessage) -> NoReturn:
        raise OperationalError("INSERT", {}, Exception("the database is down"))

    monkeypatch.setattr(service, "append_message", refuse)

    with pytest.raises(OperationalError):
        turn(make_runner(script.model), conversation_id, MESSAGE)

    assert script.requests == []
    assert guard_is_free(service, conversation_id)


def test_a_resume_appends_only_the_reply_and_keeps_the_note_out_of_the_transcript(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    script = Script([say(REPLY), say(SECOND_REPLY)])
    runner = make_runner(script.model)
    turn(runner, conversation_id, MESSAGE)
    before = transcript(service, conversation_id)
    history_before = service.load_history(conversation_id)

    resume(runner, conversation_id)

    # The earlier messages are an unchanged prefix, and the reply is the only new message.
    assert transcript(service, conversation_id) == [
        *before,
        (MessageRole.ADVISOR, SECOND_REPLY, None),
    ]
    # The agent got the stored history and the note as its prompt, and the note is in its history.
    assert prompts(script.requests[1]) == [MESSAGE, NOTE]
    history = service.load_history(conversation_id)
    assert history is not None and history != history_before
    assert NOTE in history.decode()
    assert guard_is_free(service, conversation_id)


def assert_the_resume_fails_with(
    notice: NoticeCode,
    model: Model | None,
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    asked_for: NoticeCode | None = None,
) -> None:
    turn(make_runner(replying().model), conversation_id, MESSAGE)
    before = transcript(service, conversation_id)
    history = service.load_history(conversation_id)

    resume(make_runner(model), conversation_id, asked_for)

    assert transcript(service, conversation_id) == [*before, (MessageRole.NOTICE, None, notice)]
    assert service.load_history(conversation_id) == history
    assert guard_is_free(service, conversation_id)


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(lambda: None, id="no-agent"),
        pytest.param(invalid_key, id="invalid-key"),
        pytest.param(broken_model, id="other-error"),
        pytest.param(reply_with_an_unknown_number, id="number-guard"),
    ],
)
def test_a_failed_resume_stores_only_resume_failed_and_leaves_the_history(
    model: Callable[[], Model | None],
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    assert_the_resume_fails_with(
        NoticeCode.RESUME_FAILED, model(), service, conversation_id, make_runner
    )


def test_a_resume_that_takes_too_long_stores_resume_failed(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_module, "RUN_TIMEOUT", 0.5)

    assert_the_resume_fails_with(
        NoticeCode.RESUME_FAILED, never_answering(), service, conversation_id, make_runner
    )


def test_the_failure_notice_is_the_one_the_caller_asks_for(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
) -> None:
    assert_the_resume_fails_with(
        NoticeCode.AGENT_FAILED,
        broken_model(),
        service,
        conversation_id,
        make_runner,
        asked_for=NoticeCode.AGENT_FAILED,
    )


def test_a_resume_waits_for_the_guard_of_a_run_that_is_working(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held_at = service.try_start_run(conversation_id)
    assert held_at is not None
    waits: list[float] = []

    async def let_the_other_run_finish(seconds: float) -> None:
        waits.append(seconds)
        service.finish_run(conversation_id, held_at)

    monkeypatch.setattr(runner_module, "sleep", let_the_other_run_finish)

    resume(make_runner(replying().model), conversation_id)

    assert waits == [runner_module.GUARD_POLL]
    assert transcript(service, conversation_id) == [(MessageRole.ADVISOR, REPLY, None)]
    assert guard_is_free(service, conversation_id)


def test_a_resume_that_never_gets_the_guard_stores_its_failure_notice(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    monkeypatch: pytest.MonkeyPatch,
    run_lines: Callable[[], list[dict[str, object]]],
) -> None:
    monkeypatch.setattr(runner_module, "GUARD_WAIT", 0.05)
    monkeypatch.setattr(runner_module, "GUARD_POLL", 0.01)
    held_at = service.try_start_run(conversation_id)
    assert held_at is not None
    script = replying()

    resume(make_runner(script.model), conversation_id)

    assert transcript(service, conversation_id) == [
        (MessageRole.NOTICE, None, NoticeCode.RESUME_FAILED)
    ]
    assert script.requests == []
    assert service.load_history(conversation_id) is None
    # The guard still belongs to the run that holds it.
    assert service.state(conversation_id).agent.running
    service.finish_run(conversation_id, held_at)
    [line] = run_lines()
    assert (line["event"], line["stage"], line["ok"]) == ("agent_resume", "no_guard", False)


def test_a_resume_never_raises_when_even_the_notice_cannot_be_stored(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    monkeypatch: pytest.MonkeyPatch,
    run_lines: Callable[[], list[dict[str, object]]],
) -> None:
    def refuse(conversation: UUID, message: NewMessage) -> NoReturn:
        raise OperationalError("INSERT", {}, Exception(f"the database is down, salary {SALARY}"))

    monkeypatch.setattr(service, "append_message", refuse)

    resume(make_runner(broken_model()), conversation_id)

    assert guard_is_free(service, conversation_id)
    [line] = run_lines()
    assert (line["stage"], line["ok"], line["error_type"]) == (
        "store_failed",
        False,
        "OperationalError",
    )
    assert str(SALARY) not in json.dumps(line)


def test_a_resume_of_an_unknown_conversation_does_not_raise(
    make_runner: Callable[[Model | None], AgentRunner],
    run_lines: Callable[[], list[dict[str, object]]],
) -> None:
    resume(make_runner(replying().model), UUID(int=1))

    [line] = run_lines()
    assert (line["stage"], line["error_type"]) == ("store_failed", "ConversationNotFound")


def test_a_guard_that_cannot_be_released_does_not_turn_a_reply_into_a_failure(
    service: ConversationService,
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    monkeypatch: pytest.MonkeyPatch,
    run_lines: Callable[[], list[dict[str, object]]],
) -> None:
    def refuse(conversation: UUID, started_at: object) -> NoReturn:
        raise OperationalError("UPDATE", {}, Exception("the database is down"))

    monkeypatch.setattr(service, "finish_run", refuse)

    resume(make_runner(replying().model), conversation_id)

    assert transcript(service, conversation_id) == [(MessageRole.ADVISOR, REPLY, None)]
    release, run = run_lines()
    assert (release["event"], release["error_type"]) == (
        "agent_guard_release_failed",
        "OperationalError",
    )
    assert (run["event"], run["stage"], run["ok"]) == ("agent_resume", "replied", True)


def test_each_run_writes_one_line_and_none_quotes_a_message_a_reply_a_note_or_an_error(
    conversation_id: UUID,
    make_runner: Callable[[Model | None], AgentRunner],
    run_lines: Callable[[], list[dict[str, object]]],
) -> None:
    working = make_runner(replying().model)
    turn(working, conversation_id, MESSAGE)
    turn(make_runner(broken_model()), conversation_id, AGAIN)
    resume(working, conversation_id)
    resume(make_runner(invalid_key()), conversation_id)

    lines = run_lines()

    assert [(line["event"], line["stage"], line["ok"]) for line in lines] == [
        ("agent_turn", "replied", True),
        ("agent_turn", "failed", False),
        ("agent_resume", "replied", True),
        ("agent_resume", "unavailable", False),
    ]
    assert [line.get("error_type") for line in lines] == [
        None,
        "RuntimeError",
        None,
        "ModelHTTPError",
    ]
    for line in lines:
        assert set(line) <= {
            "ts",
            "level",
            "event",
            "stage",
            "ok",
            "duration_ms",
            "conversation_id",
            "error_type",
        }
        assert line["conversation_id"] == str(conversation_id)
        assert isinstance(line["duration_ms"], float) and line["duration_ms"] >= 0
    written = json.dumps(lines, ensure_ascii=False)
    for private in (MESSAGE, AGAIN, REPLY, SECOND_REPLY, NOTE, str(SALARY), "salary"):
        assert private not in written


def answered_with_a_participation(
    ctx: RunContext[AdvisorDeps], service: ConversationService
) -> Answered:
    sent = ask_rep(ctx, RepField.NEW_CAR_PARTICIPATION, ATTO_3_KEY)
    assert isinstance(sent, RepSent)
    outcome = answer(
        service,
        sent.question_id,
        ParticipationAnswer(
            amount=Decimal("350.50"), effective_date=date(2026, 1, 1), note="מתחיל בינואר"
        ),
    )
    assert isinstance(outcome, Answered)
    return outcome


def test_the_note_of_a_participation_names_the_field_and_the_car_and_holds_no_figure(
    ctx: RunContext[AdvisorDeps], service: ConversationService
) -> None:
    note = answer_note(answered_with_a_participation(ctx, service))

    assert note.startswith("Note from the application, not from the employee")
    assert "(new_car_participation)" in note
    assert copy_he.MISSING_FIELD_LABELS[MissingField.NEW_CAR_PARTICIPATION] in note
    assert "for BYD Atto 3." in note
    for figure in ("350", "2026-01-01", "01/01/2026", "מתחיל בינואר"):
        assert figure not in note


def test_the_note_of_a_text_answer_quotes_the_words_of_the_representative_on_one_line(
    ctx: RunContext[AdvisorDeps], service: ConversationService
) -> None:
    # Line breaks of several kinds, among them two that JSON leaves as they are.
    words = 'הרכב הוא "מודל" 3\r\nשנת\u2028 2023\x85דגם'
    sent = ask_rep(ctx, RepField.CURRENT_CAR)
    assert isinstance(sent, RepSent)
    outcome = answer(service, sent.question_id, TextAnswer(text=words))
    assert isinstance(outcome, Answered)

    note = answer_note(outcome)

    assert "(current_car)" in note
    assert copy_he.MISSING_FIELD_LABELS[MissingField.CURRENT_CAR] in note
    assert json.dumps('הרכב הוא "מודל" 3 שנת 2023 דגם', ensure_ascii=False) in note
    assert len(note.splitlines()) == 1
    assert "not a tool result" in note
