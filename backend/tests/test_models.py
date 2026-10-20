from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.conversations.models import NO_CAR, ConversationRow, RepQuestionRow

NOW = datetime(2026, 3, 1, 9, 30)
CAR_KEY = "2026-2020-1-01229-00001"
WITH_AND_WITHOUT_CAR = pytest.mark.parametrize("car_key", [CAR_KEY, NO_CAR], ids=["car", "no_car"])


def add_conversation(sessions: sessionmaker[Session]) -> UUID:
    conversation_id = uuid4()
    with sessions.begin() as session:
        session.add(
            ConversationRow(
                id=conversation_id,
                created_at=NOW,
                employee_number=4821,
                profile="{}",
                alternatives="[]",
            )
        )
    return conversation_id


def open_question(conversation_id: UUID, car_key: str) -> RepQuestionRow:
    return RepQuestionRow(
        conversation_id=conversation_id,
        question_type="participation",
        missing_field="new_car_participation",
        car_key=car_key,
        question_text="What is the employee participation for this car?",
        employee_number=4821,
        status="open",
        created_at=NOW,
    )


def test_the_four_tables_exist(db_engine: Engine) -> None:
    assert set(inspect(db_engine).get_table_names()) == {
        "usage_values",
        "conversations",
        "messages",
        "rep_questions",
    }


@WITH_AND_WITHOUT_CAR
def test_a_second_open_question_for_the_same_combination_is_rejected(
    sessions: sessionmaker[Session], car_key: str
) -> None:
    conversation_id = add_conversation(sessions)
    with sessions.begin() as session:
        session.add(open_question(conversation_id, car_key))

    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(open_question(conversation_id, car_key))


@WITH_AND_WITHOUT_CAR
def test_a_new_open_question_is_accepted_once_the_first_is_answered(
    sessions: sessionmaker[Session], car_key: str
) -> None:
    conversation_id = add_conversation(sessions)
    with sessions.begin() as session:
        session.add(open_question(conversation_id, car_key))
    with sessions.begin() as session:
        session.execute(
            update(RepQuestionRow)
            .where(RepQuestionRow.conversation_id == conversation_id)
            .values(status="answered", answered_at=NOW)
        )

    with sessions.begin() as session:
        session.add(open_question(conversation_id, car_key))

    with sessions() as session:
        statuses = session.scalars(
            select(RepQuestionRow.status).order_by(RepQuestionRow.status)
        ).all()
    assert statuses == ["answered", "open"]
