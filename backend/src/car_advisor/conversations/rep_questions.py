from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from car_advisor.conversations.models import NO_CAR, ConversationRow, RepQuestionRow
from car_advisor.conversations.service import (
    ConversationConflict,
    ConversationNotFound,
    ConversationService,
)
from car_advisor.db import as_utc, utc_now
from car_advisor.engine import copy_he
from car_advisor.engine.comparison import CAR_FIELDS
from car_advisor.engine.config import CatalogCar
from car_advisor.engine.types import CarKey, EmployeeProfile, Group, MissingField, Money

# The longest name or occupation that a question repeats from what the employee typed. It keeps
# the text within its 1,000 characters and the name within its column, whatever was typed.
_MAX_TYPED: Final = 100

# The widths of the answer_note and answer_text columns. An answer that is accepted must fit
# them, because SQL Server refuses a longer value and SQLite would store it unchecked.
_NOTE_MAX: Final = 500
_TEXT_MAX: Final = 2000

# A change is applied once, and once more on freshly read data if a concurrent edit got in first,
# as in ConversationService.
_ATTEMPTS: Final = 2


class RepQuestionType(StrEnum):
    PARTICIPATION = "participation"
    OCCUPATION_WAIVER = "occupation_waiver"
    OTHER = "other"


class RepQuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"


class RepField(StrEnum):
    """The fields that only Transport_Rep can resolve. Each value is a MissingField value."""

    NEW_CAR_PARTICIPATION = "new_car_participation"
    OCCUPATION = "occupation"
    CURRENT_CAR = "current_car"
    CURRENT_CAR_PARTICIPATION = "current_car_participation"

    @property
    def missing_field(self) -> MissingField:
        return MissingField(self.value)

    @property
    def question_type(self) -> RepQuestionType:
        match self:
            case RepField.NEW_CAR_PARTICIPATION:
                return RepQuestionType.PARTICIPATION
            case RepField.OCCUPATION:
                return RepQuestionType.OCCUPATION_WAIVER
            case RepField.CURRENT_CAR | RepField.CURRENT_CAR_PARTICIPATION:
                # Only the participation in a new car has a structured answer (Req 10.5). The
                # participation in the current car is free text, which the agent relays to the
                # employee (design, Open Questions 12).
                return RepQuestionType.OTHER


class RepEmployee(BaseModel, frozen=True):
    # The employee's display name, else a generated reference such as "Employee 4821".
    display_name: str
    number: int


class RepCar(BaseModel, frozen=True):
    display_name: str | None
    car_key: CarKey


class StoredAnswer(BaseModel, frozen=True):
    """The answer of Transport_Rep as stored.

    A participation answer has an amount and its effective date, and may have a note. Any other
    answer is the text alone.
    """

    amount: Decimal | None
    effective_date: date | None
    note: str | None
    text: str | None


class RepQuestionView(BaseModel, frozen=True):
    """A question as Rep_Screen shows it.

    It holds the ID of the question and the fields of Req 12.3, with the answer once there is one.
    The conversation ID and everything in the profile are left out on purpose, because the
    Rep_Screen needs no login, and the conversation ID alone opens the employee's screen
    (Req 12.4, 12.5).
    """

    id: UUID
    question_type: RepQuestionType
    question_text: str
    created_at: datetime
    employee: RepEmployee
    eligibility_group: Group | None
    car: RepCar | None
    status: RepQuestionStatus
    answer: StoredAnswer | None


class ParticipationAnswer(BaseModel, frozen=True, hide_input_in_errors=True):
    """The answer to a participation question: an amount, and the date it takes effect from.

    The amount is stored exactly as given (Req 6.1), so it must fit the DECIMAL(12,2) column
    without rounding. The effective date is for information only and never changes the amount
    (Req 6.19). A rejected value stays out of the error text, and `loc` of each error names the
    invalid field.
    """

    amount: Annotated[Money, Field(max_digits=12, decimal_places=2)]
    effective_date: date
    note: Annotated[str, Field(max_length=_NOTE_MAX)] | None = None


class TextAnswer(BaseModel, frozen=True, hide_input_in_errors=True):
    """The free-text answer to any other question, with a character other than whitespace."""

    text: Annotated[str, Field(max_length=_TEXT_MAX)]

    @field_validator("text")
    @classmethod
    def _not_blank(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("must contain a character other than whitespace")
        return text


type SubmittedAnswer = ParticipationAnswer | TextAnswer


@dataclass(frozen=True)
class Answered:
    """The answer is stored and the comparison recalculated, both committed.

    Resuming the conversation is up to the caller, who needs the conversation ID and the field.
    They are not in the view, because that is what Rep_Screen receives (Req 12.5).
    """

    question: RepQuestionView
    conversation_id: UUID
    field: RepField


@dataclass(frozen=True)
class AlreadyAnswered:
    """The question has an answer already, which stays as it is (Req 10.8)."""


@dataclass(frozen=True)
class QuestionNotFound:
    """No question has this ID."""


@dataclass(frozen=True)
class InvalidAnswer:
    """The question is open and takes the other kind of answer (Req 10.5, 10.6).

    The fields are the names of the form fields that the question needs and this answer lacks,
    so Rep_Screen can show an error next to each (Req 12.7). They never hold a value.
    """

    fields: tuple[str, ...]


type AnswerOutcome = Answered | AlreadyAnswered | QuestionNotFound | InvalidAnswer


def open_or_get(
    conversations: ConversationService,
    session: Session,
    conversation_id: UUID,
    field: RepField,
    car: CatalogCar | None,
) -> tuple[RepQuestionRow, bool]:
    """Open the question for this conversation, field and car, or return the one that is open.

    Returns the row, to be read before the caller's transaction ends, and whether this call
    created it. Requests for the same combination, also concurrent ones, get the same question
    (Req 10.4). Creating a question marks the missing field as sent to Transport_Rep, so the
    comparison is recalculated in the caller's transaction, and returning an open question
    changes nothing.

    A StaleDataError at the caller's commit means that a concurrent edit got in first, and rolls
    the new question back with its comparison, so the caller repeats the call once.

    The car counts only for the fields that belong to a car, as in the missing items of the
    engine. Raises ConversationNotFound, and ValueError when the field lacks what its text needs:
    a car for the participation, the occupation in the profile for the waiver.
    """
    conversation = session.get(
        ConversationRow, conversation_id, options=[defer(ConversationRow.agent_history)]
    )
    if conversation is None:
        raise ConversationNotFound
    profile = EmployeeProfile.model_validate_json(conversation.profile)
    group = profile.eligibility_group

    asked_car = car if field.missing_field in CAR_FIELDS else None
    car_key = NO_CAR if asked_car is None else str(asked_car.official_key)
    label = _clip(profile.display_name or "") or copy_he.employee_label(
        conversation.employee_number
    )
    subject = copy_he.rep_question_subject(label, group)
    question = RepQuestionRow(
        conversation_id=conversation_id,
        question_type=field.question_type.value,
        missing_field=field.value,
        car_key=car_key,
        car_display_name=None if asked_car is None else asked_car.display_name,
        question_text=_question_text(field, subject, asked_car, profile.occupation),
        employee_display_name=label,
        employee_number=conversation.employee_number,
        eligibility_group=None if group is None else group.value,
        status=RepQuestionStatus.OPEN.value,
        created_at=utc_now(),
    )
    try:
        with session.begin_nested():
            session.add(question)
    except IntegrityError:
        existing = session.scalars(
            select(RepQuestionRow).where(
                RepQuestionRow.conversation_id == conversation_id,
                RepQuestionRow.missing_field == field.value,
                RepQuestionRow.car_key == car_key,
                RepQuestionRow.status == RepQuestionStatus.OPEN.value,
            )
        ).one_or_none()
        if existing is None:
            # Not the index of the open questions, so it is not a duplicate.
            raise
        return existing, False
    conversations.recalculate(session, conversation_id)
    return question, True


def answer(
    conversations: ConversationService, question_id: UUID, submitted: SubmittedAnswer
) -> AnswerOutcome:
    """Store the answer to an open question and recalculate its conversation, in one transaction.

    One conditional UPDATE decides which of several answers to the same question counts: it
    stores an answer only while the question is open and takes this kind of answer, so it matches
    for the first and for none after it (Req 10.7, 10.8). The recalculation then applies a
    Participation to the car of the question, with its effective date (Req 6.17, 6.19).

    The answer is committed before this returns, so the question is `answered` before the caller
    resumes the conversation. Every other outcome leaves the question as it was. A question that
    is answered already refuses any answer as AlreadyAnswered, and an open one refuses the wrong
    kind as InvalidAnswer (Req 10.5, 10.6). The values themselves are checked when the answer is
    built, by the models, which raise a ValidationError.

    A StaleDataError at the commit means that a concurrent edit of the conversation got in first.
    It rolls the answer back with the recalculation, so the call is repeated once on fresh data,
    and ConversationConflict is raised if that goes stale too.
    """
    for _ in range(_ATTEMPTS):
        try:
            return _answer_once(conversations, question_id, submitted)
        except StaleDataError:
            continue
    raise ConversationConflict("the conversation was changed by another request")


def list_for_rep(sessions: sessionmaker[Session]) -> list[RepQuestionView]:
    """List every question: the open ones first, then the answered ones, oldest first in each."""
    open_first = case((RepQuestionRow.status == RepQuestionStatus.OPEN.value, 0), else_=1)
    with sessions() as session:
        # The ID breaks a tie of creation times, so the order never changes between calls.
        rows = session.scalars(
            select(RepQuestionRow).order_by(
                open_first, RepQuestionRow.created_at, RepQuestionRow.id
            )
        ).all()
        return [_view(row) for row in rows]


def _answer_once(
    conversations: ConversationService, question_id: UUID, submitted: SubmittedAnswer
) -> AnswerOutcome:
    with conversations.transaction() as session:
        if not _store(session, question_id, submitted):
            return _not_stored(session, question_id, submitted)
        question = session.get_one(RepQuestionRow, question_id)
        conversations.recalculate(session, question.conversation_id)
        return Answered(
            question=_view(question),
            conversation_id=question.conversation_id,
            field=RepField(question.missing_field),
        )


def _store(session: Session, question_id: UUID, submitted: SubmittedAnswer) -> bool:
    """Store the answer if the question is open and takes this kind of answer."""
    match submitted:
        case ParticipationAnswer():
            takes = RepQuestionRow.question_type == RepQuestionType.PARTICIPATION.value
            stored: dict[str, object] = {
                "participation_amount": submitted.amount,
                "participation_effective_date": submitted.effective_date,
                "answer_note": submitted.note,
            }
        case TextAnswer():
            takes = RepQuestionRow.question_type != RepQuestionType.PARTICIPATION.value
            stored = {"answer_text": submitted.text}
    store = (
        update(RepQuestionRow)
        .where(
            RepQuestionRow.id == question_id,
            RepQuestionRow.status == RepQuestionStatus.OPEN.value,
            takes,
        )
        .values(status=RepQuestionStatus.ANSWERED.value, answered_at=utc_now(), **stored)
    )
    # On the connection, because that result type has the row count.
    return session.connection().execute(store).rowcount == 1


def _not_stored(
    session: Session, question_id: UUID, submitted: SubmittedAnswer
) -> AlreadyAnswered | QuestionNotFound | InvalidAnswer:
    status = session.scalar(select(RepQuestionRow.status).where(RepQuestionRow.id == question_id))
    if status is None:
        return QuestionNotFound()
    if status == RepQuestionStatus.ANSWERED.value:
        return AlreadyAnswered()
    # The question is open, so the answer was refused for its kind.
    match submitted:
        case TextAnswer():
            return InvalidAnswer(fields=("amount", "effective_date"))
        case ParticipationAnswer():
            return InvalidAnswer(fields=("text",))


def _question_text(
    field: RepField, subject: str, car: CatalogCar | None, occupation: str | None
) -> str:
    match field:
        case RepField.NEW_CAR_PARTICIPATION:
            if car is None:
                raise ValueError("a participation question needs a car")
            return copy_he.new_car_participation_question(
                subject, car.display_name, car.official_key
            )
        case RepField.OCCUPATION:
            recorded = _clip(occupation or "")
            if not recorded:
                raise ValueError("an occupation question needs the occupation in the profile")
            return copy_he.occupation_waiver_question(subject, recorded)
        case RepField.CURRENT_CAR:
            return copy_he.current_car_question(subject)
        case RepField.CURRENT_CAR_PARTICIPATION:
            return copy_he.current_car_participation_question(subject)


def _clip(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= _MAX_TYPED:
        return stripped
    return f"{stripped[: _MAX_TYPED - 1]}…"


def _view(row: RepQuestionRow) -> RepQuestionView:
    return RepQuestionView(
        id=row.id,
        question_type=RepQuestionType(row.question_type),
        question_text=row.question_text,
        created_at=as_utc(row.created_at),
        employee=RepEmployee(
            display_name=row.employee_display_name or copy_he.employee_label(row.employee_number),
            number=row.employee_number,
        ),
        eligibility_group=None if row.eligibility_group is None else Group(row.eligibility_group),
        car=None
        if row.car_key == NO_CAR
        else RepCar(display_name=row.car_display_name, car_key=CarKey.parse(row.car_key)),
        status=RepQuestionStatus(row.status),
        answer=_stored_answer(row),
    )


def _stored_answer(row: RepQuestionRow) -> StoredAnswer | None:
    if row.participation_amount is None and row.answer_text is None:
        return None
    return StoredAnswer(
        amount=row.participation_amount,
        effective_date=row.participation_effective_date,
        note=row.answer_note,
        text=row.answer_text,
    )
