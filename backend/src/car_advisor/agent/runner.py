"""Runs of Advisor_Agent on one conversation: a chat turn, and the resume after an answer.

A run takes the run guard of its conversation first, so that only one run works on a conversation
at a time (Req 10.10), and releases it when the run is over, however it ended. A run that fails
leaves the stored agent history as it was and ends with a notice in the conversation instead of a
reply (Req 9.15, 9.18, 10.15).

The runs are coroutines for the one event loop of the server. The HTTP client of the model provider
is shared and belongs to the loop that first used it, so a loop of its own for each run would fail
every second request with "Event loop is closed". The database layer is synchronous, so each of
its calls goes to a worker thread, as the calls of the tools do.
"""

import json
import logging
from asyncio import sleep, to_thread, wait_for
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from time import monotonic, perf_counter
from typing import Final
from uuid import UUID

from pydantic_ai import Agent, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest

from car_advisor.agent.tools import AdvisorDeps
from car_advisor.conversations.rep_questions import Answered
from car_advisor.conversations.service import (
    RUN_GUARD_TTL,
    ConversationService,
    MessageRole,
    NewMessage,
    NoticeCode,
)
from car_advisor.dataset.lookup import CarLookup
from car_advisor.engine import copy_he
from car_advisor.logs import AgentRunEvent, log_agent_run
from car_advisor.rules.loader import AppConfig

# The time one run has for the agent (Req 9.18). It is shorter than RUN_GUARD_TTL, so a run that is
# still working holds its guard when the time is up.
RUN_TIMEOUT: Final = 60.0
# A resume waits for the guard as long as a guard lives, so that the guard of a run that crashed is
# old enough to be taken over by the last try.
GUARD_WAIT: Final = RUN_GUARD_TTL.total_seconds()
GUARD_POLL: Final = 1.0

_TURN: Final = "agent_turn"
_RESUME: Final = "agent_resume"
_logger = logging.getLogger(__name__)


class AgentBusy(Exception):
    """A run holds the guard of the conversation, so the message was not accepted (Req 10.10)."""


class _Outcome(StrEnum):
    """How a run ended. The value is the `stage` of its log line."""

    REPLIED = "replied"
    # No agent, or the provider refused the API key.
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    # A resume that found the guard held by another run until it gave up.
    NO_GUARD = "no_guard"
    # A database error while storing a message or a notice.
    STORE_FAILED = "store_failed"


@dataclass(frozen=True)
class _Attempt:
    outcome: _Outcome
    # The type of the exception behind the outcome, never its message, which can quote a salary.
    error_type: str | None = None


def _failed(error: Exception) -> _Attempt:
    error_type = type(error).__name__
    if isinstance(error, TimeoutError):
        return _Attempt(_Outcome.TIMED_OUT, error_type)
    # Sending the message again does not change the answer to a refused key (Req 9.15).
    if isinstance(error, ModelHTTPError) and error.status_code == 401:
        return _Attempt(_Outcome.UNAVAILABLE, error_type)
    return _Attempt(_Outcome.FAILED, error_type)


def _turn_notice(outcome: _Outcome) -> NoticeCode:
    if outcome is _Outcome.UNAVAILABLE:
        return NoticeCode.AGENT_UNAVAILABLE
    return NoticeCode.AGENT_FAILED


def _history_json(messages: Sequence[ModelMessage]) -> bytes:
    # Each request keeps the instructions it was sent with, about 16 KB of them. They are rendered
    # again for every request, so the history that is replayed needs none.
    bare = [
        replace(message, instructions=None) if isinstance(message, ModelRequest) else message
        for message in messages
    ]
    return ModelMessagesTypeAdapter.dump_json(bare)


class AgentRunner:
    """Runs Advisor_Agent on a conversation and stores how each run ended.

    The agent is None while App_Settings has no API key. Every run then ends with a notice.
    """

    def __init__(
        self,
        advisor: Agent[AdvisorDeps, str] | None,
        conversations: ConversationService,
        lookup: CarLookup,
        config: AppConfig,
    ) -> None:
        self._advisor = advisor
        self._conversations = conversations
        self._lookup = lookup
        self._config = config

    @property
    def agent_available(self) -> bool:
        return self._advisor is not None

    async def run_turn(self, conversation_id: UUID, text: str) -> None:
        """Answer a message of the employee: store it, run the agent on it and store the reply.

        A run that fails is not an error of the caller. It ends with a notice instead of the reply,
        and the stored agent history stays as it was: `agent_unavailable` without an agent or when
        the provider refuses the key (Req 9.15), and `agent_failed` for any other error and when
        the agent takes more than RUN_TIMEOUT (Req 9.18). The message stays in the conversation.
        The caller reads how the run ended from the stored conversation.

        Raises AgentBusy, with nothing stored, while another run holds the guard (Req 10.10), and
        ConversationNotFound for an unknown conversation. A database error while storing the
        message or the notice is not caught.
        """
        guard = await to_thread(self._conversations.try_start_run, conversation_id)
        if guard is None:
            raise AgentBusy
        began = perf_counter()
        attempt = _Attempt(_Outcome.STORE_FAILED)
        try:
            employee_message = NewMessage(MessageRole.EMPLOYEE, text)
            await to_thread(self._conversations.append_message, conversation_id, employee_message)
            attempt = await self._answer(conversation_id, text, failure_notice=None)
        except Exception as error:
            attempt = _Attempt(_Outcome.STORE_FAILED, type(error).__name__)
            raise
        finally:
            await self._release(conversation_id, guard)
            _log_run(_TURN, conversation_id, began, attempt)

    async def run_with_note(
        self,
        conversation_id: UUID,
        note: str,
        failure_notice: NoticeCode = NoticeCode.RESUME_FAILED,
    ) -> None:
        """Run the agent on a note of the application, which the employee never sees.

        For the resume after the representative answered (Req 10.10, 10.11), which a FastAPI
        background task calls, and for the identification turn after a document is confirmed. The
        note is the prompt of the run and enters the agent history only. Nothing is stored as a
        message of the employee, and the only new message is the reply.

        The run waits up to GUARD_WAIT for the guard. When the run fails, or never gets the guard,
        it stores `failure_notice` instead of the reply and leaves the agent history as it was
        (Req 10.15), whether the agent is missing, refused its key, failed or timed out.

        Never raises, so the background task cannot fail: a database error that keeps even the
        notice from being stored is only logged, by type.
        """
        began = perf_counter()
        attempt = _Attempt(_Outcome.STORE_FAILED)
        guard: datetime | None = None
        try:
            guard = await self._take_guard(conversation_id)
            if guard is None:
                attempt = _Attempt(_Outcome.NO_GUARD)
                await self._store_notice(conversation_id, failure_notice)
            else:
                attempt = await self._answer(conversation_id, note, failure_notice)
        except Exception as error:
            attempt = _Attempt(_Outcome.STORE_FAILED, type(error).__name__)
        finally:
            if guard is not None:
                await self._release(conversation_id, guard)
            _log_run(_RESUME, conversation_id, began, attempt)

    async def _answer(
        self, conversation_id: UUID, prompt: str, failure_notice: NoticeCode | None
    ) -> _Attempt:
        """Run the agent on the prompt, and store its reply or the notice that tells why not.

        The notice is stored while the guard is still held, because a screen that sees the guard
        free stops waiting for a message.
        """
        attempt = await self._attempt(conversation_id, prompt)
        if attempt.outcome is not _Outcome.REPLIED:
            notice = failure_notice or _turn_notice(attempt.outcome)
            await self._store_notice(conversation_id, notice)
        return attempt

    async def _attempt(self, conversation_id: UUID, prompt: str) -> _Attempt:
        """Run the agent on the stored history and the prompt, and store the reply and the history.

        Every error ends as an outcome, because the caller stores a notice for it.
        """
        advisor = self._advisor
        if advisor is None:
            return _Attempt(_Outcome.UNAVAILABLE)
        try:
            stored = await to_thread(self._conversations.load_history, conversation_id)
            history = None if stored is None else ModelMessagesTypeAdapter.validate_json(stored)
            deps = AdvisorDeps(conversation_id, self._conversations, self._lookup, self._config)
            # A sync tool runs in a worker thread, which cannot be stopped. When the time is up,
            # wait_for cancels the await and stops waiting for the thread: the caller releases the
            # guard on time, and the tool finishes on its own and stores its writes late. That is
            # acceptable, because every tool is idempotent or a single transaction.
            result = await wait_for(
                advisor.run(prompt, message_history=history, deps=deps), RUN_TIMEOUT
            )
            # save_turn refuses a blank reply before it stores anything, which counts as a failure.
            await to_thread(
                self._conversations.save_turn,
                conversation_id,
                _history_json(result.all_messages()),
                result.output,
            )
        except Exception as error:
            return _failed(error)
        return _Attempt(_Outcome.REPLIED)

    async def _store_notice(self, conversation_id: UUID, notice: NoticeCode) -> None:
        message = NewMessage(MessageRole.NOTICE, notice=notice)
        await to_thread(self._conversations.append_message, conversation_id, message)

    async def _take_guard(self, conversation_id: UUID) -> datetime | None:
        deadline = monotonic() + GUARD_WAIT
        while True:
            guard = await to_thread(self._conversations.try_start_run, conversation_id)
            left = deadline - monotonic()
            if guard is not None or left <= 0:
                return guard
            await sleep(min(GUARD_POLL, left))

    async def _release(self, conversation_id: UUID, guard: datetime) -> None:
        try:
            await to_thread(self._conversations.finish_run, conversation_id, guard)
        except Exception as error:
            # Not raised, which would turn the outcome of a run that is over into a failure. The
            # guard frees itself after RUN_GUARD_TTL.
            _logger.warning(
                "agent_guard_release_failed",
                extra={"conversation_id": str(conversation_id), "error_type": type(error).__name__},
            )


def _log_run(event: AgentRunEvent, conversation_id: UUID, began: float, attempt: _Attempt) -> None:
    log_agent_run(
        event,
        stage=attempt.outcome.value,
        ok=attempt.outcome is _Outcome.REPLIED,
        duration_ms=round((perf_counter() - began) * 1000, 1),
        conversation_id=conversation_id,
        error_type=attempt.error_type,
    )


def answer_note(answered: Answered) -> str:
    """Describe an answer of the representative to the agent, in English (Req 10.11).

    The note names the field, and for a participation also the car. It holds no figure of a
    participation: the comparison, which the answer already changed, carries the amount and the
    effective date. A free-text answer is quoted as the words of the representative, and the agent
    cannot repeat its digits, because a note is not a tool result (Req 9.5).
    """
    field = answered.field
    label = copy_he.MISSING_FIELD_LABELS[field.missing_field]
    stored = answered.question.answer
    text = None if stored is None else stored.text
    if stored is not None and stored.choice is not None:
        # The choice is already stored in the profile, as the occupation or the group (Req 10.17).
        chosen = json.dumps(stored.choice, ensure_ascii=False)
        return (
            f"Note from the application, not from the employee: the transport representative "
            f"answered the question about {label} ({field.value}) with the choice {chosen}. "
            "The stored profile and the comparison already follow it."
        )
    opening = (
        "Note from the application, not from the employee: the transport representative "
        f"answered the question about {label} ({field.value})"
    )
    if text is None:
        car = answered.question.car
        about = "" if car is None or not car.display_name else f" for {car.display_name}"
        return f"{opening}{about}. The answer is already in the comparison."
    # Every kind of whitespace becomes one space, so that no line break, not even U+2028, can start
    # a new line of the note.
    quoted = json.dumps(" ".join(text.split()), ensure_ascii=False)
    return (
        f"{opening}, in free text: {quoted}. It is not a tool result, so repeat none of its digits."
    )
