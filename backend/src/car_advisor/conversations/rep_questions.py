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
from car_advisor.engine.eligibility import employee_group
from car_advisor.engine.types import CarKey, EmployeeProfile, Group, MissingField, Money
from car_advisor.rules.loader import AppConfig

# The longest name or occupation that a question repeats from what the employee typed. It keeps
# the text within its 1,000 characters and the name within its column, whatever was typed.
_MAX_TYPED: Final = 100

# The widths of the answer_note, answer_text and answer_choice columns. An answer that is accepted
# must fit them, because SQL Server refuses a longer value and SQLite would store it unchecked.
_NOTE_MAX: Final = 500
_TEXT_MAX: Final = 2000
_CHOICE_MAX: Final = 200

# A change is applied once, and once more on freshly read data if a concurrent edit got in first,
# as in ConversationService.
_ATTEMPTS: Final = 2


class RepQuestionType(StrEnum):
    PARTICIPATION = "participation"
    # A choice among the names of Occupations_Table.
    OCCUPATION = "occupation"
    # A choice among the groups A-D.
    ELIGIBILITY_GROUP = "eligibility_group"
    OTHER = "other"


# The types whose answer is a choice, and the form field that each of the other types needs.
_CHOICE_TYPES: Final = frozenset({RepQuestionType.OCCUPATION, RepQuestionType.ELIGIBILITY_GROUP})


class RepQuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"


class RepField(StrEnum):
    """The fields that only Transport_Rep can resolve. Each value is a MissingField value."""

    NEW_CAR_PARTICIPATION = "new_car_participation"
    OCCUPATION = "occupation"
    ELIGIBILITY_GROUP = "eligibility_group"
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
                return RepQuestionType.OCCUPATION
            case RepField.ELIGIBILITY_GROUP:
                return RepQuestionType.ELIGIBILITY_GROUP
            case RepField.CURRENT_CAR | RepField.CURRENT_CAR_PARTICIPATION:
                # Only the participation in a new car has a structured answer (Req 10.5). The
                # participation in the current car is free text, which the agent relays to the
                # employee (design, Open Questions 7).
                return RepQuestionType.OTHER


class RepEmployee(BaseModel, frozen=True):
    # The employee's display name, else a generated reference such as "Employee 4821".
    display_name: str
    number: int


class RepCar(BaseModel, frozen=True):
    display_name: str | None
    car_key: CarKey


class RepChoice(BaseModel, frozen=True):
    """One choice of an occupation or eligibility group question: the stored value and its label."""

    value: str
    label: str


class StoredAnswer(BaseModel, frozen=True):
    """The answer of Transport_Rep as stored.

    A participation answer has an amount and its effective date, and may have a note. A choice
    answer is the chosen table name or group, and any other answer is the text alone.
    """

    amount: Decimal | None
    effective_date: date | None
    note: str | None
    text: str | None
    choice: str | None = None


class RepQuestionView(BaseModel, frozen=True):
    """A question as Rep_Screen shows it.

    It holds the ID of the question and the fields of Req 12.3, the catalog amount of the car and
    the choices of the question, with the answer once there is one. The conversation ID and
    everything in the profile are left out on purpose, because the Rep_Screen needs no login, and
    the conversation ID alone opens the employee's screen (Req 12.4, 12.5).
    """

    id: UUID
    question_type: RepQuestionType
    question_text: str
    created_at: datetime
    employee: RepEmployee
    eligibility_group: Group | None
    car: RepCar | None
    # The Default_Participation of the car of a participation question, else None (Req 12.6).
    suggested_amount: Decimal | None = None
    # The choices of an occupation or eligibility group question, else None.
    choices: tuple[RepChoice, ...] | None = None
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


class ChoiceAnswer(BaseModel, frozen=True, hide_input_in_errors=True):
    """The answer to an occupation or eligibility group question: one of its choices.

    Which values are offered depends on the question, so `answer` checks the value against the
    choices of the question it answers (Req 10.6).
    """

    choice: Annotated[str, Field(min_length=1, max_length=_CHOICE_MAX)]


type SubmittedAnswer = ParticipationAnswer | ChoiceAnswer | TextAnswer


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
    """The question is open and refuses this answer, for its kind or its choice (Req 10.5, 10.6).

    The fields are the names of the form fields that the question needs and this answer lacks,
    so Rep_Screen can show an error next to each (Req 12.7). They never hold a value.
    """

    fields: tuple[str, ...]


type AnswerOutcome = Answered | AlreadyAnswered | QuestionNotFound | InvalidAnswer


def choices_for(question_type: RepQuestionType, config: AppConfig) -> tuple[RepChoice, ...] | None:
    """The choices of a question type: the table names, the four groups, or None for no choice."""
    match question_type:
        case RepQuestionType.OCCUPATION:
            names = (item.name.strip() for item in config.occupations.occupations)
            return tuple(RepChoice(value=name, label=name) for name in names)
        case RepQuestionType.ELIGIBILITY_GROUP:
            return tuple(
                RepChoice(value=group.value, label=copy_he.GROUP_LETTERS[group])
                for group in config.rules.groups.order
            )
        case _:
            return None


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
    a car for the participation, the occupation in the profile for the occupation and the group.
    """
    conversation = session.get(
        ConversationRow, conversation_id, options=[defer(ConversationRow.agent_history)]
    )
    if conversation is None:
        raise ConversationNotFound
    profile = EmployeeProfile.model_validate_json(conversation.profile)
    # The group in use, from the occupation or the representative (Req 5.23).
    group = employee_group(profile, conversations.config.occupations)

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
    for the first and for none after it (Req 10.7, 10.8). A choice answer is written to the
    profile in the same transaction: the table name as the occupation, the group as the
    Group_Override (Req 10.17). The recalculation then applies it, and a Participation to the car
    of the question, with its effective date (Req 6.17, 6.19).

    The answer is committed before this returns, so the question is `answered` before the caller
    resumes the conversation. Every other outcome leaves the question as it was. A question that
    is answered already refuses any answer as AlreadyAnswered, and an open one refuses the wrong
    kind, or a choice it does not offer, as InvalidAnswer (Req 10.5, 10.6). The values themselves
    are checked when the answer is built, by the models, which raise a ValidationError.

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


def list_for_rep(sessions: sessionmaker[Session], config: AppConfig) -> list[RepQuestionView]:
    """List every question: the open ones first, then the answered ones, oldest first in each."""
    open_first = case((RepQuestionRow.status == RepQuestionStatus.OPEN.value, 0), else_=1)
    with sessions() as session:
        # The ID breaks a tie of creation times, so the order never changes between calls.
        rows = session.scalars(
            select(RepQuestionRow).order_by(
                open_first, RepQuestionRow.created_at, RepQuestionRow.id
            )
        ).all()
        return [_view(row, config) for row in rows]


def _answer_once(
    conversations: ConversationService, question_id: UUID, submitted: SubmittedAnswer
) -> AnswerOutcome:
    config = conversations.config
    with conversations.transaction() as session:
        # The UPDATE comes first and alone decides: a read before it would make concurrent
        # answers wait on each other's locks instead of one of them simply matching no row.
        if not _store(session, question_id, _types_taking(submitted, config), submitted):
            return _not_stored(session, question_id)
        question = session.get_one(RepQuestionRow, question_id)
        if isinstance(submitted, ChoiceAnswer):
            question_type = RepQuestionType(question.question_type)
            conversations.apply_rep_answer(
                session, question.conversation_id, _profile_change(question_type, submitted)
            )
        conversations.recalculate(session, question.conversation_id)
        return Answered(
            question=_view(question, config),
            conversation_id=question.conversation_id,
            field=RepField(question.missing_field),
        )


def _types_taking(submitted: SubmittedAnswer, config: AppConfig) -> frozenset[RepQuestionType]:
    """The question types that accept this answer, its choice included."""
    match submitted:
        case ParticipationAnswer():
            return frozenset({RepQuestionType.PARTICIPATION})
        case TextAnswer():
            return frozenset({RepQuestionType.OTHER})
        case ChoiceAnswer(choice=choice):
            return frozenset(
                question_type
                for question_type in _CHOICE_TYPES
                if choice in {item.value for item in choices_for(question_type, config) or ()}
            )


def _not_stored(
    session: Session, question_id: UUID
) -> AlreadyAnswered | QuestionNotFound | InvalidAnswer:
    found = session.execute(
        select(RepQuestionRow.status, RepQuestionRow.question_type).where(
            RepQuestionRow.id == question_id
        )
    ).one_or_none()
    if found is None:
        return QuestionNotFound()
    status, question_type = found
    if status == RepQuestionStatus.ANSWERED.value:
        return AlreadyAnswered()
    # The question is open, so the answer was refused for its kind or its choice.
    return InvalidAnswer(fields=_fields_needed(RepQuestionType(question_type)))


def _fields_needed(question_type: RepQuestionType) -> tuple[str, ...]:
    if question_type is RepQuestionType.PARTICIPATION:
        return ("amount", "effective_date")
    if question_type in _CHOICE_TYPES:
        return ("choice",)
    return ("text",)


def _profile_change(question_type: RepQuestionType, submitted: ChoiceAnswer) -> dict[str, object]:
    if question_type is RepQuestionType.OCCUPATION:
        return {"occupation": submitted.choice}
    return {"eligibility_group": submitted.choice}


def _store(
    session: Session,
    question_id: UUID,
    types: frozenset[RepQuestionType],
    submitted: SubmittedAnswer,
) -> bool:
    """Store the answer if the question is open and of a type that takes it."""
    if not types:
        return False
    match submitted:
        case ParticipationAnswer():
            stored: dict[str, object] = {
                "participation_amount": submitted.amount,
                "participation_effective_date": submitted.effective_date,
                "answer_note": submitted.note,
            }
        case TextAnswer():
            stored = {"answer_text": submitted.text}
        case ChoiceAnswer():
            stored = {"answer_choice": submitted.choice}
    store = (
        update(RepQuestionRow)
        .where(
            RepQuestionRow.id == question_id,
            RepQuestionRow.status == RepQuestionStatus.OPEN.value,
            RepQuestionRow.question_type.in_(sorted(item.value for item in types)),
        )
        .values(status=RepQuestionStatus.ANSWERED.value, answered_at=utc_now(), **stored)
    )
    # On the connection, because that result type has the row count.
    return session.connection().execute(store).rowcount == 1


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
            return copy_he.occupation_question(subject, recorded)
        case RepField.ELIGIBILITY_GROUP:
            return copy_he.eligibility_group_question(subject, _clip(occupation or "") or None)
        case RepField.CURRENT_CAR:
            return copy_he.current_car_question(subject)
        case RepField.CURRENT_CAR_PARTICIPATION:
            return copy_he.current_car_participation_question(subject)


def _clip(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= _MAX_TYPED:
        return stripped
    return f"{stripped[: _MAX_TYPED - 1]}…"


def _view(row: RepQuestionRow, config: AppConfig) -> RepQuestionView:
    question_type = RepQuestionType(row.question_type)
    car_key = None if row.car_key == NO_CAR else CarKey.parse(row.car_key)
    catalog_car = None if car_key is None else config.catalog.find(car_key)
    suggested = (
        catalog_car.default_participation
        if catalog_car is not None and question_type is RepQuestionType.PARTICIPATION
        else None
    )
    return RepQuestionView(
        id=row.id,
        question_type=question_type,
        question_text=row.question_text,
        created_at=as_utc(row.created_at),
        employee=RepEmployee(
            display_name=row.employee_display_name or copy_he.employee_label(row.employee_number),
            number=row.employee_number,
        ),
        eligibility_group=None if row.eligibility_group is None else Group(row.eligibility_group),
        car=None if car_key is None else RepCar(display_name=row.car_display_name, car_key=car_key),
        suggested_amount=suggested,
        choices=choices_for(question_type, config),
        status=RepQuestionStatus(row.status),
        answer=_stored_answer(row),
    )


def _stored_answer(row: RepQuestionRow) -> StoredAnswer | None:
    if row.participation_amount is None and row.answer_text is None and row.answer_choice is None:
        return None
    return StoredAnswer(
        amount=row.participation_amount,
        effective_date=row.participation_effective_date,
        note=row.answer_note,
        text=row.answer_text,
        choice=row.answer_choice,
    )
