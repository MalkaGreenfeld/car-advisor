"""Smoke tests against a live SQL Server, such as the db container.

TEST_MSSQL_URL gives the server and login, for example
`mssql+mssqlpython://sa:<DB_PASSWORD>@127.0.0.1:1433/master`. Each run creates its own
databases and drops them afterwards, so the database part of the URL is not used.
"""

import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Thread
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, make_url, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from car_advisor.conversations.models import ConversationRow, MessageRow, RepQuestionRow
from car_advisor.conversations.rep_questions import (
    AlreadyAnswered,
    Answered,
    AnswerOutcome,
    InvalidAnswer,
    ParticipationAnswer,
    QuestionNotFound,
    RepField,
    RepQuestionStatus,
    StoredAnswer,
    TextAnswer,
    answer,
    list_for_rep,
    open_or_get,
)
from car_advisor.conversations.service import ConversationService, MessageRole, NewMessage
from car_advisor.dataset.importer import import_tax_year
from car_advisor.dataset.lookup import CarLookup, Found
from car_advisor.db import (
    Base,
    database_url,
    ensure_database,
    make_engine,
    make_session_factory,
    run_migrations,
)
from car_advisor.engine.types import CarKey
from car_advisor.rules.loader import load_config
from car_advisor.settings import Settings
from tests.conftest import REAL_DATASET
from tests.conversations.conftest import CONFIG_DIR
from tests.dataset.test_lookup import (
    BUSIEST_SEARCH,
    BUSIEST_SEARCH_MATCHES,
    MODEL_3,
    MODEL_3_PARTS,
    SPECIAL_NAME_PARAMS,
    candidates,
    import_searchable_names,
    timed,
)
from tests.test_models import CAR_KEY, NOW, WITH_AND_WITHOUT_CAR, add_conversation, open_question

pytestmark = [
    pytest.mark.mssql,
    pytest.mark.skipif(not os.environ.get("TEST_MSSQL_URL"), reason="TEST_MSSQL_URL is not set"),
]

HEBREW = "מה ההשתתפות העצמית ברכב החדש? ₪250 לחודש"
CAR_NAME = "קיה נירו EV"
EMPLOYEE_NAME = "דנה לוי"
OCCUPATION = "רופא שיניים"

type Schema = dict[str, tuple[list[tuple[str, str, bool]], list[str]]]


def new_database_name() -> str:
    return f"car_advisor_smoke_{uuid4().hex}"


@pytest.fixture(scope="module")
def server() -> Settings:
    url = make_url(os.environ["TEST_MSSQL_URL"])
    return Settings(
        _env_file=None,
        dataset_path=Path("unused.csv"),
        tax_year=2026,
        db_host=url.host,
        db_port=url.port or 1433,
        db_name=new_database_name(),
        db_user=url.username,
        db_password=url.password,
        llm_model="unused",
    )


@pytest.fixture(scope="module")
def master(server: Settings) -> Engine:
    return create_engine(
        database_url(server, "master"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )


@pytest.fixture(scope="module")
def engine(server: Settings, master: Engine) -> Iterator[Engine]:
    ensure_database(server)
    engine = make_engine(server)
    run_migrations(engine)
    yield engine
    engine.dispose()
    drop_database(master, server.db_name)


def database_exists(master: Engine, name: str) -> bool:
    with master.connect() as connection:
        return connection.execute(text("SELECT DB_ID(:name)"), {"name": name}).scalar() is not None


def drop_database(master: Engine, name: str) -> None:
    # The name comes from new_database_name, so it holds only identifier characters.
    with master.connect() as connection:
        connection.exec_driver_sql(f"DROP DATABASE IF EXISTS [{name}]")


def schema(engine: Engine) -> Schema:
    inspector = inspect(engine)
    return {
        table: (
            [
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspector.get_columns(table)
            ],
            sorted(str(index["name"]) for index in inspector.get_indexes(table)),
        )
        for table in inspector.get_table_names()
    }


def test_ensure_database_creates_a_missing_database(server: Settings, master: Engine) -> None:
    settings = server.model_copy(update={"db_name": new_database_name()})
    assert not database_exists(master, settings.db_name)

    try:
        ensure_database(settings)
        ensure_database(settings)

        assert database_exists(master, settings.db_name)
    finally:
        drop_database(master, settings.db_name)


def snapshot_is_on(master: Engine, name: str) -> bool:
    with master.connect() as connection:
        flag = connection.execute(
            text("SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = :name"),
            {"name": name},
        ).scalar_one()
    return bool(flag)


def test_ensure_database_turns_on_read_committed_snapshot_when_it_is_off(
    server: Settings, master: Engine
) -> None:
    settings = server.model_copy(update={"db_name": new_database_name()})
    try:
        # A database that an earlier version created, with the default isolation.
        with master.connect() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE [{settings.db_name}]")
        assert not snapshot_is_on(master, settings.db_name)

        ensure_database(settings)
        assert snapshot_is_on(master, settings.db_name)

        ensure_database(settings)
        assert snapshot_is_on(master, settings.db_name)
    finally:
        drop_database(master, settings.db_name)


def test_a_database_that_ensure_database_created_has_read_committed_snapshot_on(
    server: Settings, master: Engine, engine: Engine
) -> None:
    assert snapshot_is_on(master, server.db_name)


def test_running_the_migrations_again_is_a_no_op(engine: Engine) -> None:
    sessions = make_session_factory(engine)
    conversation_id = add_conversation(sessions)
    before = schema(engine)

    run_migrations(engine)

    assert schema(engine) == before
    assert set(before) == {
        "alembic_version",
        "usage_values",
        "conversations",
        "messages",
        "rep_questions",
    }
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert context.get_current_revision() == "0002"
        # Only SQL Server shows the DATETIME2 variant, which SQLite cannot compare.
        assert compare_metadata(context, Base.metadata) == []
    with sessions() as session:
        assert session.get(ConversationRow, conversation_id) is not None


@WITH_AND_WITHOUT_CAR
def test_the_filtered_unique_index_allows_one_open_question(engine: Engine, car_key: str) -> None:
    sessions = make_session_factory(engine)
    conversation_id = add_conversation(sessions)
    with sessions.begin() as session:
        session.add(open_question(conversation_id, car_key))

    with pytest.raises(IntegrityError), sessions.begin() as session:
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
            select(RepQuestionRow.status)
            .where(RepQuestionRow.conversation_id == conversation_id)
            .order_by(RepQuestionRow.status)
        ).all()
    assert statuses == ["answered", "open"]


def test_hebrew_text_round_trips_through_nvarchar(engine: Engine) -> None:
    sessions = make_session_factory(engine)
    conversation_id = add_conversation(sessions)
    question = open_question(conversation_id, CAR_KEY)
    question.question_text = HEBREW
    question.car_display_name = CAR_NAME
    message = MessageRow(
        conversation_id=conversation_id, role="advisor", text=HEBREW, created_at=NOW
    )
    with sessions.begin() as session:
        session.add_all([question, message])

    with sessions() as session:
        message_text = session.scalars(
            select(MessageRow.text).where(MessageRow.conversation_id == conversation_id)
        ).one()
        stored = session.scalars(
            select(RepQuestionRow).where(RepQuestionRow.conversation_id == conversation_id)
        ).one()
    assert message_text == HEBREW
    assert (stored.question_text, stored.car_display_name) == (HEBREW, CAR_NAME)


def test_a_conversation_keeps_its_run_guard_and_agent_history(engine: Engine) -> None:
    sessions = make_session_factory(engine)
    config = load_config(
        CONFIG_DIR / "rules.yaml", CONFIG_DIR / "car_catalog.yaml", CONFIG_DIR / "occupations.yaml"
    )
    service = ConversationService(sessions, CarLookup(sessions, 2026), config)
    conversation_id = service.create()
    service.append_message(conversation_id, NewMessage(MessageRole.EMPLOYEE, HEBREW))
    # Far more than the 4,000 characters of a plain NVARCHAR, with Hebrew in every entry.
    history = json.dumps([{"content": HEBREW}] * 2_000, ensure_ascii=False).encode()

    started_at = service.try_start_run(conversation_id)
    assert started_at is not None
    assert service.try_start_run(conversation_id) is None
    # The state reads the messages and then the open questions, and SQL Server allows the
    # second statement only once the first result has been read in full.
    assert service.state(conversation_id).agent.running is True
    service.save_turn(conversation_id, history, HEBREW)
    assert service.load_history(conversation_id) == history

    # DATETIME2 keeps the microseconds, so only the exact start time releases the guard.
    service.finish_run(conversation_id, started_at + timedelta(microseconds=1))
    assert service.state(conversation_id).agent.running is True
    service.finish_run(conversation_id, started_at)
    state = service.state(conversation_id)
    assert state.agent.running is False
    assert [message.text for message in state.messages] == [HEBREW, HEBREW]


def rep_question_service(engine: Engine) -> tuple[ConversationService, UUID]:
    sessions = make_session_factory(engine)
    config = load_config(
        CONFIG_DIR / "rules.yaml", CONFIG_DIR / "car_catalog.yaml", CONFIG_DIR / "occupations.yaml"
    )
    # The conversation holds no car, so the comparison needs no record of the dataset.
    service = ConversationService(sessions, CarLookup(sessions, 2026), config)
    conversation_id = service.create()
    service.update_profile(
        conversation_id, {"display_name": EMPLOYEE_NAME, "occupation": OCCUPATION}
    )
    return service, conversation_id


def test_a_rep_question_asked_twice_is_one_open_question_listed_in_order(engine: Engine) -> None:
    service, conversation_id = rep_question_service(engine)
    sessions = make_session_factory(engine)

    with sessions.begin() as session:
        first, created = open_or_get(service, session, conversation_id, RepField.OCCUPATION, None)
        again, created_again = open_or_get(
            service, session, conversation_id, RepField.OCCUPATION, None
        )
        # The duplicate rolled back only its savepoint, so the transaction goes on.
        other, _ = open_or_get(service, session, conversation_id, RepField.CURRENT_CAR, None)
        first_id, other_id = first.id, other.id
        assert (created, created_again, again is first) == (True, False, True)

    # The database holds the questions of the other tests of this module as well.
    listed = [view for view in list_for_rep(sessions) if view.id in {first_id, other_id}]
    assert [view.id for view in listed] == [first_id, other_id]
    assert OCCUPATION in listed[0].question_text
    assert listed[0].employee.display_name == EMPLOYEE_NAME


def test_two_requests_for_the_same_rep_question_get_the_same_one(engine: Engine) -> None:
    service, conversation_id = rep_question_service(engine)
    sessions = make_session_factory(engine)
    second: list[tuple[UUID, bool]] = []

    def second_request() -> None:
        with sessions.begin() as session:
            question, created = open_or_get(
                service, session, conversation_id, RepField.CURRENT_CAR, None
            )
            second.append((question.id, created))

    with sessions.begin() as session:
        question, created = open_or_get(
            service, session, conversation_id, RepField.CURRENT_CAR, None
        )
        first_id = question.id
        worker = Thread(target=second_request)
        worker.start()
        # The second INSERT waits on the key of the first one, which is not committed yet.
        worker.join(timeout=1)
        waited = worker.is_alive()
    worker.join(timeout=30)

    assert (created, waited, worker.is_alive()) == (True, True, False)
    assert second == [(first_id, False)]


def ask_participation(engine: Engine) -> tuple[ConversationService, UUID]:
    """A service, and the ID of an open question about the participation in a catalog car."""
    service, conversation_id = rep_question_service(engine)
    config = load_config(
        CONFIG_DIR / "rules.yaml", CONFIG_DIR / "car_catalog.yaml", CONFIG_DIR / "occupations.yaml"
    )
    car = config.catalog.find(CarKey.parse("2026-2025-1-01014-00003"))
    assert car is not None
    with make_session_factory(engine).begin() as session:
        question, _ = open_or_get(
            service, session, conversation_id, RepField.NEW_CAR_PARTICIPATION, car
        )
        return service, question.id


def test_an_answer_is_stored_exactly_and_the_second_one_is_refused(engine: Engine) -> None:
    service, question_id = ask_participation(engine)
    sessions = make_session_factory(engine)
    amount = Decimal("1234567.89")
    submitted = ParticipationAnswer(amount=amount, effective_date=date(2026, 1, 1), note=HEBREW)

    outcome = answer(service, question_id, submitted)
    later = [
        answer(service, question_id, submitted),
        answer(service, question_id, TextAnswer(text=HEBREW)),
        answer(service, uuid4(), submitted),
    ]

    assert isinstance(outcome, Answered)
    stored = StoredAnswer(amount=amount, effective_date=date(2026, 1, 1), note=HEBREW, text=None)
    assert [view.answer for view in list_for_rep(sessions) if view.id == question_id] == [stored]
    assert later == [AlreadyAnswered(), AlreadyAnswered(), QuestionNotFound()]


def test_text_sent_to_a_participation_question_is_invalid_and_leaves_it_open(
    engine: Engine,
) -> None:
    service, question_id = ask_participation(engine)
    sessions = make_session_factory(engine)

    outcome = answer(service, question_id, TextAnswer(text=HEBREW))

    assert outcome == InvalidAnswer(fields=("amount", "effective_date"))
    [view] = [view for view in list_for_rep(sessions) if view.id == question_id]
    assert (view.status, view.answer) == (RepQuestionStatus.OPEN, None)


def test_answers_that_race_for_one_question_store_exactly_one(engine: Engine) -> None:
    service, question_id = ask_participation(engine)
    sessions = make_session_factory(engine)
    racers = 8
    start = Barrier(racers, timeout=30)

    def submit(racer: int) -> AnswerOutcome:
        start.wait()
        submitted = ParticipationAnswer(
            amount=Decimal(300 + racer), effective_date=date(2026, 1, 1), note=str(racer)
        )
        return answer(service, question_id, submitted)

    with ThreadPoolExecutor(max_workers=racers) as pool:
        outcomes = list(pool.map(submit, range(racers), timeout=60))

    [winner] = [racer for racer, outcome in enumerate(outcomes) if isinstance(outcome, Answered)]
    assert outcomes.count(AlreadyAnswered()) == racers - 1
    [view] = [view for view in list_for_rep(sessions) if view.id == question_id]
    assert view.answer is not None
    assert (view.answer.amount, view.answer.note) == (Decimal(300 + winner), str(winner))


@pytest.fixture(scope="module")
def lookup(engine: Engine) -> CarLookup:
    sessions = make_session_factory(engine)
    import_tax_year(sessions, REAL_DATASET, 2026)
    return CarLookup(sessions, 2026)


def test_a_key_lookup_takes_under_100_ms(lookup: CarLookup) -> None:
    result, seconds = timed(lambda: lookup.get(MODEL_3_PARTS))

    assert result == Found(MODEL_3)
    assert seconds < 0.1


def test_a_search_takes_under_500_ms(lookup: CarLookup) -> None:
    year, text = BUSIEST_SEARCH

    result, seconds = timed(lambda: lookup.search(year, text))

    assert len(candidates(result).items) == BUSIEST_SEARCH_MATCHES
    assert seconds < 0.5


# [ is the one that only SQL Server reads as a wildcard, which the SQLite tests cannot show.
@pytest.mark.parametrize("name", SPECIAL_NAME_PARAMS)
def test_special_characters_in_the_search_text_match_only_themselves(
    engine: Engine, tmp_path: Path, name: str
) -> None:
    sessions = make_session_factory(engine)
    import_searchable_names(sessions, tmp_path, 1999)

    result = candidates(CarLookup(sessions, 1999).search(2020, name))

    assert [candidate.trade_name for candidate in result.items] == [name]
