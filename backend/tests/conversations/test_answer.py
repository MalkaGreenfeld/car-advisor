import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, update
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.conversations.models import ConversationRow, RepQuestionRow
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
    SubmittedAnswer,
    TextAnswer,
    answer,
    list_for_rep,
)
from car_advisor.conversations.service import (
    ComparisonView,
    ConversationConflict,
    ConversationService,
)
from car_advisor.dataset.lookup import CarLookup
from car_advisor.db import Base, utc_now
from car_advisor.engine.config import CatalogCar
from car_advisor.engine.types import MissingField, RepParticipation
from car_advisor.rules.loader import AppConfig
from tests.conversations.test_recalculation import EngineView
from tests.conversations.test_rep_questions import (
    ATTO_3,
    ATTO_3_KEY,
    NIRO,
    NIRO_KEY,
    PROFILE,
    ask,
    catalog_car,
    comparison_of,
    stored_version,
)

D = Decimal
AMOUNT = D("350.50")
EFFECTIVE_DATE = date(2026, 1, 1)
NOTE = "מתחיל בינואר"
PARTICIPATION = ParticipationAnswer(amount=AMOUNT, effective_date=EFFECTIVE_DATE, note=NOTE)
# The spaces and the line break are part of the text, which is stored as it was typed.
TEXT = TextAnswer(text="  כן, אפשר לוותר\nעל הרכב  ")
TEXT_FIELDS = [RepField.OCCUPATION, RepField.CURRENT_CAR, RepField.CURRENT_CAR_PARTICIPATION]
RIGHT_KIND: dict[RepField, SubmittedAnswer] = {
    RepField.NEW_CAR_PARTICIPATION: PARTICIPATION,
    **{field: TEXT for field in TEXT_FIELDS},
}

NOTE_WIDTH = RepQuestionRow.__table__.c.answer_note.type.length
TEXT_WIDTH = RepQuestionRow.__table__.c.answer_text.type.length
RACERS = 8


@pytest.fixture
def conversation_id(dataset_service: ConversationService) -> UUID:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, PROFILE)
    return conversation_id


@pytest.fixture
def atto_3(app_config: AppConfig) -> CatalogCar:
    return catalog_car(app_config, ATTO_3_KEY)


@pytest.fixture
def engine_view(app_config: AppConfig, real_sessions: sessionmaker[Session]) -> EngineView:
    return EngineView(app_config, CarLookup(real_sessions, 2026))


def snapshot(sessions: sessionmaker[Session], question_id: UUID) -> dict[str, object]:
    """Every column of the question as it is stored."""
    with sessions() as session:
        row = session.get_one(RepQuestionRow, question_id)
        return {column.key: getattr(row, column.key) for column in inspect(row).mapper.column_attrs}


def stored_answer(row: dict[str, object]) -> tuple[object, ...]:
    """The columns that hold the answer: amount, effective date, note and text."""
    keys = ("participation_amount", "participation_effective_date", "answer_note", "answer_text")
    return tuple(row[key] for key in keys)


def test_a_valid_participation_answer_is_stored_exactly_and_reaches_the_comparison(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
    engine_view: EngineView,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3])
    question_id, _ = ask(
        dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )
    version = stored_version(sessions, conversation_id)
    before = utc_now()

    outcome = answer(dataset_service, question_id, PARTICIPATION)

    after = utc_now()
    stored = snapshot(sessions, question_id)
    assert stored["status"] == RepQuestionStatus.ANSWERED
    assert stored_answer(stored) == (AMOUNT, EFFECTIVE_DATE, NOTE, None)
    answered_at = stored["answered_at"]
    assert isinstance(answered_at, datetime)
    assert before <= answered_at <= after
    [listed] = list_for_rep(sessions)
    assert listed.answer == StoredAnswer(
        amount=AMOUNT, effective_date=EFFECTIVE_DATE, note=NOTE, text=None
    )
    assert outcome == Answered(
        question=listed, conversation_id=conversation_id, field=RepField.NEW_CAR_PARTICIPATION
    )

    state = dataset_service.state(conversation_id)
    sent = frozenset({(MissingField.NEW_CAR_PARTICIPATION, ATTO_3_KEY)})
    answers = {ATTO_3_KEY: RepParticipation(amount=AMOUNT, effective_date=EFFECTIVE_DATE)}
    assert state.comparison == engine_view(state, answers, sent)
    [column] = comparison_of(dataset_service, conversation_id).alternatives
    assert (column.status, column.missing) == ("calculated", ())
    # The table shows whole shekels, while the stored amount keeps its agorot.
    assert (column.participation, column.participation_effective_date) == (351, EFFECTIVE_DATE)
    assert state.open_questions == ()
    assert stored_version(sessions, conversation_id) == version + 1


def test_the_participation_applies_only_to_the_car_the_question_is_about(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    app_config: AppConfig,
    atto_3: CatalogCar,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3, NIRO])
    niro = catalog_car(app_config, NIRO_KEY)
    atto_3_id, _ = ask(
        dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )
    ask(dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, niro)

    answer(dataset_service, atto_3_id, PARTICIPATION)

    answered, waiting = comparison_of(dataset_service, conversation_id).alternatives
    assert (answered.status, answered.participation) == ("calculated", 351)
    assert (waiting.status, waiting.participation) == ("pending", None)
    assert [(item.field, item.sent_to_rep) for item in waiting.missing] == [
        (MissingField.NEW_CAR_PARTICIPATION, True)
    ]


def test_the_effective_date_is_shown_and_never_changes_the_amount(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3])
    columns = []
    for effective_date in (date(2026, 1, 1), date(2031, 12, 31)):
        question_id, _ = ask(
            dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
        )
        answer(
            dataset_service,
            question_id,
            ParticipationAnswer(amount=AMOUNT, effective_date=effective_date),
        )
        [column] = comparison_of(dataset_service, conversation_id).alternatives
        columns.append(column)

    early, late = columns
    assert early.participation_effective_date == date(2026, 1, 1)
    assert late.participation_effective_date == date(2031, 12, 31)
    undated = {"participation_effective_date": None}
    assert early.model_copy(update=undated) == late.model_copy(update=undated)


@pytest.mark.parametrize("field", TEXT_FIELDS)
def test_a_text_answer_is_stored_exactly_and_applies_no_participation(
    field: RepField,
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3])
    question_id, _ = ask(dataset_service, sessions, conversation_id, field, atto_3)
    comparison = comparison_of(dataset_service, conversation_id)

    outcome = answer(dataset_service, question_id, TEXT)

    stored = snapshot(sessions, question_id)
    assert stored["status"] == RepQuestionStatus.ANSWERED
    assert stored_answer(stored) == (None, None, None, TEXT.text)
    assert isinstance(outcome, Answered)
    assert (outcome.field, outcome.conversation_id) == (field, conversation_id)
    assert outcome.question.answer == StoredAnswer(
        amount=None, effective_date=None, note=None, text=TEXT.text
    )
    assert comparison_of(dataset_service, conversation_id) == comparison


@pytest.mark.parametrize(
    ("field", "wrong", "fields"),
    [
        pytest.param(
            RepField.NEW_CAR_PARTICIPATION,
            TEXT,
            ("amount", "effective_date"),
            id="text-to-participation",
        ),
        *(
            pytest.param(field, PARTICIPATION, ("text",), id=f"participation-to-{field.value}")
            for field in TEXT_FIELDS
        ),
    ],
)
def test_an_answer_of_the_wrong_kind_is_invalid_and_the_question_stays_open(
    field: RepField,
    wrong: SubmittedAnswer,
    fields: tuple[str, ...],
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3])
    question_id, _ = ask(dataset_service, sessions, conversation_id, field, atto_3)
    stored = snapshot(sessions, question_id)
    version = stored_version(sessions, conversation_id)
    comparison = comparison_of(dataset_service, conversation_id)

    outcome = answer(dataset_service, question_id, wrong)

    assert outcome == InvalidAnswer(fields=fields)
    assert snapshot(sessions, question_id) == stored
    assert stored["status"] == RepQuestionStatus.OPEN
    assert stored_version(sessions, conversation_id) == version
    assert comparison_of(dataset_service, conversation_id) == comparison
    assert isinstance(answer(dataset_service, question_id, RIGHT_KIND[field]), Answered)


def test_a_second_answer_gets_already_answered_and_changes_nothing(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3])
    question_id, _ = ask(
        dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )
    first = answer(dataset_service, question_id, PARTICIPATION)
    stored = snapshot(sessions, question_id)
    version = stored_version(sessions, conversation_id)
    comparison = comparison_of(dataset_service, conversation_id)
    again = ParticipationAnswer(amount=D(999), effective_date=date(2027, 1, 1), note="again")

    # A later answer is refused for an answered question whatever its kind.
    outcomes = [
        answer(dataset_service, question_id, later) for later in (again, PARTICIPATION, TEXT)
    ]

    assert isinstance(first, Answered)
    assert outcomes == [AlreadyAnswered()] * 3
    assert snapshot(sessions, question_id) == stored
    assert stored_version(sessions, conversation_id) == version
    assert comparison_of(dataset_service, conversation_id) == comparison
    assert list_for_rep(sessions) == [first.question]


def test_an_unknown_question_is_not_found_and_nothing_is_stored(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    question_id, _ = ask(
        dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )
    stored = snapshot(sessions, question_id)
    version = stored_version(sessions, conversation_id)

    outcomes = [answer(dataset_service, uuid4(), submitted) for submitted in (PARTICIPATION, TEXT)]

    assert outcomes == [QuestionNotFound()] * 2
    assert snapshot(sessions, question_id) == stored
    assert stored_version(sessions, conversation_id) == version


def test_an_answer_whose_recalculation_fails_is_not_stored(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question_id, _ = ask(
        dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )
    stored = snapshot(sessions, question_id)

    def fail(session: Session, conversation: UUID) -> ComparisonView:
        raise RuntimeError("the comparison could not be calculated")

    monkeypatch.setattr(dataset_service, "recalculate", fail)

    with pytest.raises(RuntimeError):
        answer(dataset_service, question_id, PARTICIPATION)

    assert snapshot(sessions, question_id) == stored


class ConcurrentEdits:
    """Bumps the version of a conversation after each recalculation of the first `attempts` calls.

    This is what another request does when it edits the conversation after the answer has read
    it. The bump runs on the connection of the answer, so that it is rolled back with the answer.
    """

    def __init__(self, service: ConversationService, attempts: int) -> None:
        self._recalculate = service.recalculate
        self._attempts = attempts
        self.calls = 0

    def __call__(self, session: Session, conversation_id: UUID) -> ComparisonView:
        view = self._recalculate(session, conversation_id)
        self.calls += 1
        if self.calls <= self._attempts:
            session.connection().execute(
                update(ConversationRow)
                .where(ConversationRow.id == conversation_id)
                .values(version=ConversationRow.version + 1)
            )
        return view


def test_a_concurrent_edit_is_retried_once_and_the_answer_is_stored(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3])
    question_id, _ = ask(
        dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )
    version = stored_version(sessions, conversation_id)
    edits = ConcurrentEdits(dataset_service, attempts=1)
    monkeypatch.setattr(dataset_service, "recalculate", edits)

    outcome = answer(dataset_service, question_id, PARTICIPATION)

    assert isinstance(outcome, Answered)
    assert edits.calls == 2
    assert stored_answer(snapshot(sessions, question_id)) == (AMOUNT, EFFECTIVE_DATE, NOTE, None)
    [column] = comparison_of(dataset_service, conversation_id).alternatives
    assert (column.status, column.participation) == ("calculated", 351)
    # The first attempt was rolled back with the edit that made it stale.
    assert stored_version(sessions, conversation_id) == version + 1


def test_a_conversation_that_keeps_changing_raises_a_conflict_and_the_question_stays_open(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3])
    question_id, _ = ask(
        dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )
    stored = snapshot(sessions, question_id)
    version = stored_version(sessions, conversation_id)
    comparison = comparison_of(dataset_service, conversation_id)
    edits = ConcurrentEdits(dataset_service, attempts=2)
    monkeypatch.setattr(dataset_service, "recalculate", edits)

    with pytest.raises(ConversationConflict) as caught:
        answer(dataset_service, question_id, PARTICIPATION)

    assert edits.calls == 2
    assert "350" not in str(caught.value)
    assert NOTE not in str(caught.value)
    assert snapshot(sessions, question_id) == stored
    assert stored_version(sessions, conversation_id) == version
    assert comparison_of(dataset_service, conversation_id) == comparison


@pytest.fixture
def file_sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    # A connection for each thread, as on SQL Server. The in-memory database of the other tests
    # shares one connection, which mixes the transactions of threads.
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'race.db'}",
        connect_args={"autocommit": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(engine)
    engine.dispose()


def test_answers_that_race_for_one_question_store_exactly_one(
    file_sessions: sessionmaker[Session],
    real_sessions: sessionmaker[Session],
    app_config: AppConfig,
    atto_3: CatalogCar,
) -> None:
    service = ConversationService(file_sessions, CarLookup(real_sessions, 2026), app_config)
    conversation_id = service.create()
    service.update_profile(conversation_id, PROFILE)
    service.set_alternatives(conversation_id, [ATTO_3])
    question_id, _ = ask(
        service, file_sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )
    version = stored_version(file_sessions, conversation_id)
    submissions = [
        ParticipationAnswer(amount=D(300 + racer), effective_date=EFFECTIVE_DATE, note=str(racer))
        for racer in range(RACERS)
    ]
    start = threading.Barrier(RACERS, timeout=30)

    def submit(submitted: ParticipationAnswer) -> AnswerOutcome:
        start.wait()
        return answer(service, question_id, submitted)

    with ThreadPoolExecutor(max_workers=RACERS) as pool:
        outcomes = list(pool.map(submit, submissions, timeout=60))

    [winner] = [racer for racer, outcome in enumerate(outcomes) if isinstance(outcome, Answered)]
    assert outcomes.count(AlreadyAnswered()) == RACERS - 1
    stored = snapshot(file_sessions, question_id)
    assert stored["status"] == RepQuestionStatus.ANSWERED
    assert stored_answer(stored) == (D(300 + winner), EFFECTIVE_DATE, str(winner), None)
    # Only the answer that counted recalculated, and the comparison holds its amount.
    assert stored_version(file_sessions, conversation_id) == version + 1
    [column] = comparison_of(service, conversation_id).alternatives
    assert (column.status, column.participation) == ("calculated", 300 + winner)


@pytest.mark.parametrize(
    "amount", [D(0), "0", "350", "350.5", "350.500", 350, "9999999999.99"], ids=repr
)
def test_a_participation_answer_takes_an_amount_of_at_least_zero(amount: object) -> None:
    submitted = ParticipationAnswer.model_validate(
        {"amount": amount, "effective_date": EFFECTIVE_DATE}
    )

    assert submitted.amount == D(str(amount))
    assert submitted.note is None


@pytest.mark.parametrize(
    "amount",
    ["-0.01", "-1", "abc", "", "NaN", "Infinity", "350.555", "10000000000", None, True],
    ids=repr,
)
def test_a_participation_answer_refuses_a_missing_negative_or_unstorable_amount(
    amount: object,
) -> None:
    with pytest.raises(ValidationError) as caught:
        ParticipationAnswer.model_validate({"amount": amount, "effective_date": EFFECTIVE_DATE})

    assert [error["loc"] for error in caught.value.errors(include_input=False)] == [("amount",)]


@pytest.mark.parametrize(
    ("data", "invalid"),
    [
        pytest.param({"effective_date": EFFECTIVE_DATE}, "amount", id="no-amount"),
        pytest.param({"amount": "1"}, "effective_date", id="no-date"),
        pytest.param({"amount": "1", "effective_date": None}, "effective_date", id="null-date"),
        pytest.param({"amount": "1", "effective_date": ""}, "effective_date", id="empty-date"),
        pytest.param(
            {"amount": "1", "effective_date": "2026-02-31"}, "effective_date", id="feb-31"
        ),
        pytest.param(
            {"amount": "1", "effective_date": "2026-04-31"}, "effective_date", id="apr-31"
        ),
        pytest.param(
            {"amount": "1", "effective_date": "2023-02-29"}, "effective_date", id="no-leap"
        ),
        pytest.param({"amount": "1", "effective_date": "2026-13-01"}, "effective_date", id="month"),
        pytest.param(
            {"amount": "1", "effective_date": "01/01/2026"}, "effective_date", id="format"
        ),
        pytest.param(
            {"amount": "1", "effective_date": EFFECTIVE_DATE, "note": "x" * (NOTE_WIDTH + 1)},
            "note",
            id="long-note",
        ),
    ],
)
def test_a_participation_answer_needs_a_real_calendar_date_and_a_note_that_fits(
    data: dict[str, object], invalid: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        ParticipationAnswer.model_validate(data)

    assert [error["loc"] for error in caught.value.errors(include_input=False)] == [(invalid,)]


@pytest.mark.parametrize(
    "data",
    [
        {"amount": "1", "effective_date": "2024-02-29"},
        {"amount": "1", "effective_date": EFFECTIVE_DATE, "note": None},
        {"amount": "1", "effective_date": EFFECTIVE_DATE, "note": ""},
        {"amount": "1", "effective_date": EFFECTIVE_DATE, "note": "x" * NOTE_WIDTH},
    ],
)
def test_a_participation_answer_takes_a_leap_day_and_any_note_that_fits(
    data: dict[str, object],
) -> None:
    assert ParticipationAnswer.model_validate(data).note == data.get("note")


@pytest.mark.parametrize("text", ["כן", "  x  ", TEXT.text, "a" * TEXT_WIDTH])
def test_a_text_answer_keeps_its_text_exactly(text: str) -> None:
    assert TextAnswer(text=text).text == text


@pytest.mark.parametrize("text", ["", " ", "\t\n ", "\u00a0", "a" * (TEXT_WIDTH + 1)])
def test_a_text_answer_refuses_blank_text_and_text_that_does_not_fit(text: str) -> None:
    with pytest.raises(ValidationError) as caught:
        TextAnswer(text=text)

    assert [error["loc"] for error in caught.value.errors(include_input=False)] == [("text",)]


def test_the_error_text_of_a_refused_answer_never_holds_what_was_typed() -> None:
    with pytest.raises(ValidationError) as amount:
        ParticipationAnswer.model_validate(
            {"amount": "-98765.43", "effective_date": EFFECTIVE_DATE}
        )
    with pytest.raises(ValidationError) as text:
        TextAnswer(text="q" * (TEXT_WIDTH + 1))

    assert "98765" not in str(amount.value)
    assert "qqqq" not in str(text.value)
