from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

import car_advisor.conversations.service as service_module
from car_advisor.conversations.models import ConversationRow, MessageRow
from car_advisor.conversations.service import (
    ConversationNotFound,
    ConversationService,
    MessageRole,
    NewMessage,
)
from car_advisor.db import Base
from car_advisor.engine.comparison import Comparison, ComparisonInputs, compare
from car_advisor.engine.types import Alternative, EmployeeProfile

REPLY = "שלום, במה אפשר לעזור?"
# What the agent layer stores: the serialized message list, here with a Hebrew reply in it.
HISTORY = f'[{{"kind": "response", "parts": [{{"content": "{REPLY}"}}]}}]'.encode()


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def count(sessions: sessionmaker[Session], table: type[Base]) -> int:
    with sessions() as session:
        return session.execute(select(func.count()).select_from(table)).scalar_one()


def stored_guard(sessions: sessionmaker[Session], conversation_id: UUID) -> datetime | None:
    with sessions() as session:
        return session.get_one(ConversationRow, conversation_id).agent_run_started_at


def hold_guard_since(
    sessions: sessionmaker[Session], conversation_id: UUID, started_at: datetime
) -> None:
    with sessions.begin() as session:
        session.execute(
            update(ConversationRow)
            .where(ConversationRow.id == conversation_id)
            .values(agent_run_started_at=started_at)
        )


def start_run(service: ConversationService, conversation_id: UUID) -> datetime:
    started_at = service.try_start_run(conversation_id)
    assert started_at is not None
    return started_at


def test_try_start_run_takes_the_guard_and_returns_the_time_it_stored(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()
    before = utc_now()

    started_at = start_run(service, conversation_id)

    assert before <= started_at <= utc_now()
    assert stored_guard(sessions, conversation_id) == started_at


def test_a_second_try_start_run_fails_while_the_guard_is_held(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()
    first = start_run(service, conversation_id)

    second = service.try_start_run(conversation_id)

    assert second is None
    assert stored_guard(sessions, conversation_id) == first


def test_a_guard_older_than_90_seconds_can_be_taken(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()
    crashed_at = utc_now() - timedelta(seconds=91)
    hold_guard_since(sessions, conversation_id, crashed_at)

    started_at = start_run(service, conversation_id)

    assert started_at > crashed_at
    assert stored_guard(sessions, conversation_id) == started_at


def test_a_guard_younger_than_90_seconds_is_still_held(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()
    running_since = utc_now() - timedelta(seconds=89)
    hold_guard_since(sessions, conversation_id, running_since)

    assert service.try_start_run(conversation_id) is None

    assert stored_guard(sessions, conversation_id) == running_since


def test_the_guard_belongs_to_one_conversation(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    first, second = service.create(), service.create()
    first_started_at = start_run(service, first)
    second_started_at = start_run(service, second)

    service.finish_run(first, first_started_at)

    assert stored_guard(sessions, first) is None
    assert stored_guard(sessions, second) == second_started_at


def test_the_state_reports_the_agent_as_running_exactly_while_the_guard_is_held(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()
    assert service.state(conversation_id).agent.running is False

    started_at = start_run(service, conversation_id)
    assert service.state(conversation_id).agent.running is True

    service.finish_run(conversation_id, started_at)
    assert service.state(conversation_id).agent.running is False

    hold_guard_since(sessions, conversation_id, utc_now() - timedelta(seconds=89))
    assert service.state(conversation_id).agent.running is True

    hold_guard_since(sessions, conversation_id, utc_now() - timedelta(seconds=91))
    assert service.state(conversation_id).agent.running is False


def test_finish_run_releases_the_guard(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()
    started_at = start_run(service, conversation_id)

    service.finish_run(conversation_id, started_at)

    assert stored_guard(sessions, conversation_id) is None
    assert service.try_start_run(conversation_id) is not None


def test_finish_run_does_nothing_when_there_is_no_guard_to_release(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()

    service.finish_run(conversation_id, utc_now())
    started_at = start_run(service, conversation_id)
    service.finish_run(conversation_id, started_at)
    service.finish_run(conversation_id, started_at)
    service.finish_run(uuid4(), started_at)

    assert stored_guard(sessions, conversation_id) is None
    assert count(sessions, ConversationRow) == 1


def test_a_run_that_lost_its_guard_does_not_release_the_guard_of_the_run_that_took_over(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()
    first_started_at = utc_now() - timedelta(seconds=91)
    hold_guard_since(sessions, conversation_id, first_started_at)
    second_started_at = start_run(service, conversation_id)

    service.finish_run(conversation_id, first_started_at)

    assert stored_guard(sessions, conversation_id) == second_started_at
    assert service.try_start_run(conversation_id) is None
    service.finish_run(conversation_id, second_started_at)
    assert stored_guard(sessions, conversation_id) is None


def test_save_turn_appends_one_advisor_message_and_stores_the_history(
    service: ConversationService,
) -> None:
    conversation_id = service.create()
    service.append_message(conversation_id, NewMessage(MessageRole.EMPLOYEE, "Hello"))
    assert service.load_history(conversation_id) is None

    service.save_turn(conversation_id, HISTORY, REPLY)

    messages = service.state(conversation_id).messages
    assert [(message.role, message.text) for message in messages] == [
        (MessageRole.EMPLOYEE, "Hello"),
        (MessageRole.ADVISOR, REPLY),
    ]
    assert service.load_history(conversation_id) == HISTORY


def test_a_later_turn_replaces_the_history_and_adds_one_more_message(
    service: ConversationService,
) -> None:
    conversation_id = service.create()
    later_history = HISTORY + b" "
    service.save_turn(conversation_id, HISTORY, "first")

    service.save_turn(conversation_id, later_history, "second")

    texts = [message.text for message in service.state(conversation_id).messages]
    assert texts == ["first", "second"]
    assert service.load_history(conversation_id) == later_history


def test_save_turn_stores_the_history_and_the_reply_together_or_not_at_all(
    service: ConversationService, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = service.create()

    def fail_to_add(session: Session, conversation: UUID, message: NewMessage) -> NoReturn:
        raise RuntimeError("the reply could not be stored")

    monkeypatch.setattr(service_module, "_add_message", fail_to_add)

    with pytest.raises(RuntimeError):
        service.save_turn(conversation_id, HISTORY, REPLY)

    assert service.load_history(conversation_id) is None
    assert service.state(conversation_id).messages == ()


def test_save_turn_with_a_blank_reply_stores_nothing(service: ConversationService) -> None:
    conversation_id = service.create()

    with pytest.raises(ValueError):
        service.save_turn(conversation_id, HISTORY, "  ")

    assert service.load_history(conversation_id) is None
    assert service.state(conversation_id).messages == ()


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda service, unknown: service.try_start_run(unknown), id="try_start_run"),
        pytest.param(lambda service, unknown: service.load_history(unknown), id="load_history"),
        pytest.param(
            lambda service, unknown: service.save_turn(unknown, HISTORY, REPLY), id="save_turn"
        ),
    ],
)
def test_an_unknown_conversation_raises_not_found_and_stores_nothing(
    call: Callable[[ConversationService, UUID], object],
    service: ConversationService,
    sessions: sessionmaker[Session],
) -> None:
    with pytest.raises(ConversationNotFound):
        call(service, uuid4())

    assert count(sessions, ConversationRow) == 0
    assert count(sessions, MessageRow) == 0


def test_a_run_and_a_profile_edit_in_flight_do_not_disturb_each_other(
    service: ConversationService, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = service.create()
    calculations = 0
    started_at: list[datetime] = []

    def compare_while_a_run_starts(
        profile: EmployeeProfile, alternatives: Sequence[Alternative], inputs: ComparisonInputs
    ) -> Comparison:
        # The edit has read the conversation when the run takes the guard and stores its turn.
        nonlocal calculations
        calculations += 1
        if calculations == 1:
            started_at.append(start_run(service, conversation_id))
            service.save_turn(conversation_id, HISTORY, REPLY)
        return compare(profile, alternatives, inputs)

    monkeypatch.setattr(service_module, "compare", compare_while_a_run_starts)

    service.update_profile(conversation_id, {"base_salary": "16000"})

    # An edit that went stale would have been calculated again.
    assert calculations == 1
    state = service.state(conversation_id)
    assert state.profile.base_salary == Decimal(16000)
    assert state.agent.running is True
    assert service.load_history(conversation_id) == HISTORY
    service.finish_run(conversation_id, started_at[0])
    assert service.state(conversation_id).agent.running is False
