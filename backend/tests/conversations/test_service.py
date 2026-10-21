import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.conversations.models import NO_CAR, ConversationRow, MessageRow, RepQuestionRow
from car_advisor.conversations.service import (
    AgentStatus,
    CarView,
    ComparisonView,
    ConversationNotFound,
    ConversationService,
    ConversationState,
    EmployeeGroup,
    MessageRole,
    NewMessage,
    NoticeCode,
    OpenQuestion,
    TodayView,
    parse_conversation_id,
)
from car_advisor.db import Base
from car_advisor.engine.types import (
    Alternative,
    CarAlternative,
    CarKey,
    EmployeeProfile,
    Group,
    MissingField,
    WaiverAlternative,
)

NOW = datetime(2026, 3, 1, 9, 30)
CAR_KEY = "2026-2020-1-01229-00001"


def count(sessions: sessionmaker[Session], table: type[Base]) -> int:
    with sessions() as session:
        return session.execute(select(func.count()).select_from(table)).scalar_one()


def question(
    conversation_id: UUID,
    field: str,
    car_key: str = NO_CAR,
    *,
    car_display_name: str | None = None,
    status: str = "open",
    created_at: datetime = NOW,
) -> RepQuestionRow:
    return RepQuestionRow(
        conversation_id=conversation_id,
        question_type="other",
        missing_field=field,
        car_key=car_key,
        car_display_name=car_display_name,
        question_text="A question for the representative.",
        employee_number=4821,
        status=status,
        created_at=created_at,
    )


def test_a_created_conversation_has_an_empty_state(service: ConversationService) -> None:
    conversation_id = service.create()

    assert service.state(conversation_id) == ConversationState(
        id=conversation_id,
        messages=(),
        profile=EmployeeProfile(),
        employee_group=EmployeeGroup(group=None, source=None),
        alternatives=(),
        comparison=None,
        open_questions=(),
        agent=AgentStatus(running=False),
    )


def test_a_created_conversation_stores_an_empty_profile_and_no_history_or_guard(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()

    with sessions() as session:
        row = session.get_one(ConversationRow, conversation_id)
        assert (row.profile, row.alternatives, row.comparison) == ("{}", "[]", None)
        assert (row.agent_history, row.agent_run_started_at, row.version) == (None, None, 1)


@pytest.mark.parametrize(
    ("draw", "employee_number"),
    [
        pytest.param(lambda bound: 0, 1000, id="lowest"),
        pytest.param(lambda bound: bound - 1, 9999, id="highest"),
    ],
)
def test_the_employee_number_runs_from_1000_to_9999(
    draw: Callable[[int], int],
    employee_number: int,
    service: ConversationService,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets, "randbelow", draw)

    conversation_id = service.create()

    with sessions() as session:
        assert session.get_one(ConversationRow, conversation_id).employee_number == employee_number


def test_an_unknown_id_raises_conversation_not_found_and_creates_nothing(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    unknown = uuid4()

    with pytest.raises(ConversationNotFound):
        service.state(unknown)
    with pytest.raises(ConversationNotFound):
        service.append_message(unknown, NewMessage(MessageRole.EMPLOYEE, "Hello"))

    assert count(sessions, ConversationRow) == 0
    assert count(sessions, MessageRow) == 0


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("not-a-conversation", id="words"),
        pytest.param("6f0c1c2e", id="too-short"),
        pytest.param("6f0c1c2e-6b6e-4f7e-9a53-0d0b0f6c5e1g", id="not-hexadecimal"),
        pytest.param("../rep/questions", id="path"),
    ],
)
def test_a_malformed_id_is_as_unknown_as_a_missing_one(text: str) -> None:
    with pytest.raises(ConversationNotFound):
        parse_conversation_id(text)


def test_the_text_of_an_id_reads_back_as_the_id() -> None:
    conversation_id = uuid4()

    assert parse_conversation_id(str(conversation_id)) == conversation_id


def test_messages_come_back_in_insertion_order(service: ConversationService) -> None:
    conversation_id = service.create()
    # The texts are not in alphabetical order, so only the insertion order can explain the result.
    sent = [
        NewMessage(MessageRole.EMPLOYEE, "message 3"),
        NewMessage(MessageRole.ADVISOR, "message 1"),
        NewMessage(MessageRole.NOTICE, notice=NoticeCode.AGENT_FAILED),
        NewMessage(MessageRole.EMPLOYEE, "message 2"),
    ]
    for message in sent:
        service.append_message(conversation_id, message)

    messages = service.state(conversation_id).messages

    assert [(m.role, m.text, m.notice) for m in messages] == [
        (m.role, m.text, m.notice) for m in sent
    ]
    assert all(earlier.id < later.id for earlier, later in pairwise(messages))


def test_message_times_come_back_as_utc(service: ConversationService) -> None:
    conversation_id = service.create()
    service.append_message(conversation_id, NewMessage(MessageRole.EMPLOYEE, "Hello"))

    [message] = service.state(conversation_id).messages

    assert message.created_at.utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - message.created_at) < timedelta(minutes=1)


def test_a_notice_carries_its_code_and_no_text(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()

    service.append_message(
        conversation_id, NewMessage(MessageRole.NOTICE, notice=NoticeCode.RESUME_FAILED)
    )

    [message] = service.state(conversation_id).messages
    assert (message.role, message.text, message.notice) == (
        MessageRole.NOTICE,
        None,
        NoticeCode.RESUME_FAILED,
    )
    with sessions() as session:
        stored = session.execute(select(MessageRow.text, MessageRow.notice_code)).one()
        assert tuple(stored) == (None, "resume_failed")


@pytest.mark.parametrize(
    ("role", "text", "notice"),
    [
        pytest.param(MessageRole.NOTICE, "Sorry", NoticeCode.AGENT_FAILED, id="notice-with-text"),
        pytest.param(MessageRole.NOTICE, None, None, id="notice-without-code"),
        pytest.param(MessageRole.EMPLOYEE, "Hello", NoticeCode.AGENT_FAILED, id="text-with-code"),
        pytest.param(MessageRole.ADVISOR, None, None, id="advisor-without-text"),
        pytest.param(MessageRole.EMPLOYEE, "  ", None, id="blank-text"),
    ],
)
def test_a_message_is_either_text_or_a_notice_code(
    role: MessageRole, text: str | None, notice: NoticeCode | None
) -> None:
    with pytest.raises(ValueError, match="notice carries a code and no text"):
        NewMessage(role, text, notice)


def test_the_state_shows_what_the_conversation_stores(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = service.create()
    car_key = CarKey.parse(CAR_KEY)
    profile = EmployeeProfile(
        eligibility_group=Group.B,
        base_salary=Decimal("16000.50"),
        pay_components=[],
        has_current_car=True,
        current_car_key=car_key,
    )
    alternatives: list[Alternative] = [CarAlternative(car_key=car_key), WaiverAlternative()]
    comparison = ComparisonView(
        today=TodayView(
            status="calculated",
            car=CarView(car_key=car_key, display_name="Tesla Model 3", group=Group.C),
            participation=350,
            usage_value=7280,
            ev_refund=250,
            car_maintenance=0,
            net_pay=14235,
            missing=(),
        ),
        alternatives=(),
    )
    with sessions.begin() as session:
        session.execute(
            update(ConversationRow)
            .where(ConversationRow.id == conversation_id)
            .values(
                profile=profile.model_dump_json(),
                alternatives=TypeAdapter(list[Alternative]).dump_json(alternatives).decode(),
                comparison=comparison.model_dump_json(),
            )
        )

    state = service.state(conversation_id)

    assert state.profile == profile
    # A stored group is the representative's Group_Override.
    assert state.employee_group == EmployeeGroup(group=Group.B, source="representative")
    assert state.alternatives == tuple(alternatives)
    assert state.comparison == comparison


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        pytest.param({"occupation": "מפתחת תוכנה"}, EmployeeGroup(Group.B, "occupation"), id="occ"),
        pytest.param(
            {"occupation": "מפתח/ת תוכנה", "eligibility_group": "D"},
            EmployeeGroup(Group.D, "representative"),
            id="override",
        ),
        pytest.param({"occupation": "Gardener"}, EmployeeGroup(None, None), id="unlisted"),
        pytest.param({}, EmployeeGroup(None, None), id="nothing"),
    ],
)
def test_the_state_carries_the_group_in_use_and_its_source(
    stored: dict[str, str],
    expected: EmployeeGroup,
    service: ConversationService,
    sessions: sessionmaker[Session],
) -> None:
    conversation_id = service.create()
    with sessions.begin() as session:
        session.execute(
            update(ConversationRow)
            .where(ConversationRow.id == conversation_id)
            .values(profile=EmployeeProfile.model_validate(stored).model_dump_json())
        )

    assert service.state(conversation_id).employee_group == expected


def test_the_open_questions_are_those_of_this_conversation_without_an_answer(
    service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    this, other = service.create(), service.create()
    with sessions.begin() as session:
        session.add_all(
            [
                question(this, "occupation", created_at=NOW + timedelta(minutes=1)),
                question(this, "new_car_participation", CAR_KEY, car_display_name="Tesla Model 3"),
                question(this, "new_car_participation", CAR_KEY, status="answered"),
                question(other, "current_car_participation"),
            ]
        )

    assert service.state(this).open_questions == (
        OpenQuestion(MissingField.NEW_CAR_PARTICIPATION, "Tesla Model 3", CarKey.parse(CAR_KEY)),
        OpenQuestion(MissingField.OCCUPATION, None, None),
    )
    assert service.state(other).open_questions == (
        OpenQuestion(MissingField.CURRENT_CAR_PARTICIPATION, None, None),
    )
