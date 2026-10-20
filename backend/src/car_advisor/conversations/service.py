import secrets
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, defer, sessionmaker
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm.exc import StaleDataError

from car_advisor.conversations.models import NO_CAR, ConversationRow, MessageRow, RepQuestionRow
from car_advisor.conversations.profile import FieldError, ProfileValidationError, validate_profile
from car_advisor.dataset.lookup import CarLookup
from car_advisor.db import as_utc, utc_now
from car_advisor.engine import copy_he
from car_advisor.engine.comparison import (
    CAR_FIELDS,
    AlternativeResult,
    Comparison,
    ComparisonInputs,
    MissingItem,
    TodayResult,
    compare,
)
from car_advisor.engine.money import Amount
from car_advisor.engine.types import (
    Alternative,
    CarAlternative,
    CarKey,
    EmployeeProfile,
    Group,
    MissingField,
    RepParticipation,
)
from car_advisor.rules.loader import AppConfig

_ALTERNATIVES: Final = TypeAdapter(list[Alternative])
_OPEN: Final = "open"
_ANSWERED: Final = "answered"
# A change is applied once, and once more on freshly read data if a concurrent edit got in first.
_ATTEMPTS: Final = 2
# Longer than the 60 s timeout of an agent run, so a run that is working keeps its guard to the
# end, while one that crashed frees it by itself.
RUN_GUARD_TTL: Final = timedelta(seconds=90)


class ConversationNotFound(Exception):
    """No conversation has the requested ID, or the ID is not a UUID at all (Req 11.11)."""


def parse_conversation_id(text: str) -> UUID:
    """Read the ID that a URL carries.

    A malformed ID raises ConversationNotFound, like one that matches no conversation, so the
    caller never needs to tell the two apart.
    """
    try:
        return UUID(text)
    except ValueError:
        raise ConversationNotFound from None


class ConversationConflict(Exception):
    """Concurrent changes kept winning over this one, even after it was applied again."""


class AlternativeNotFound(Exception):
    """The conversation has no Alternative at the requested position."""


class MessageRole(StrEnum):
    EMPLOYEE = "employee"
    ADVISOR = "advisor"
    NOTICE = "notice"


class NoticeCode(StrEnum):
    AGENT_UNAVAILABLE = "agent_unavailable"
    AGENT_FAILED = "agent_failed"
    RESUME_FAILED = "resume_failed"


@dataclass(frozen=True)
class NewMessage:
    """A message to append: non-blank text from the employee or the advisor, or a notice.

    A notice is only a code, because the Employee_Screen picks its wording from its copy file.
    """

    role: MessageRole
    text: str | None = None
    notice: NoticeCode | None = None

    def __post_init__(self) -> None:
        if self.role is MessageRole.NOTICE:
            valid = self.notice is not None and self.text is None
        else:
            valid = self.notice is None and self.text is not None and bool(self.text.strip())
        if not valid:
            # Static on purpose: the text of a message can hold a salary.
            raise ValueError(
                "a notice carries a code and no text, any other message carries text and no code"
            )


@dataclass(frozen=True)
class StoredMessage:
    id: int
    role: MessageRole
    text: str | None
    notice: NoticeCode | None
    created_at: datetime


class _View(BaseModel, frozen=True):
    """Base of the comparison payload: every amount in it is a whole-shekel integer."""


class CarView(_View):
    car_key: CarKey
    # The catalog name, else the dataset's manufacturer and trade name, else None.
    display_name: str | None
    # None for a car outside the catalog.
    group: Group | None


class EligibilityView(_View):
    status: Literal["eligible", "not_eligible", "missing"]
    reason: str
    is_electric: bool | None


class MissingView(_View):
    field: MissingField
    label: str
    sent_to_rep: bool


class BreakdownView(_View):
    usage_value: int
    ev_refund: int
    participation: int
    car_maintenance: int


class TodayView(_View):
    status: Literal["calculated", "pending"]
    # None when the employee has no car, and while the current car is not identified.
    car: CarView | None
    participation: int | None
    usage_value: int | None
    ev_refund: int | None
    car_maintenance: int | None
    net_pay: int | None
    missing: tuple[MissingView, ...]


class AlternativeView(_View):
    # The index in the conversation's Alternative list, which is also the column order.
    position: int
    status: Literal["calculated", "not_eligible", "pending"]
    # None for giving up the car.
    car: CarView | None
    eligibility: EligibilityView
    car_cost: int | None
    car_budget: int | None
    participation: int | None
    participation_effective_date: date | None
    employer_gap_share: int | None
    # After the employer's share, so that a comparison stored before this field still loads.
    employee_gap_share: int | None = None
    usage_value: int | None
    ev_refund: int | None
    car_maintenance: int | None
    unused_budget: int | None
    net_pay: int | None
    difference: int | None
    breakdown: BreakdownView | None
    explanation: tuple[str, ...] | None
    missing: tuple[MissingView, ...]


class ComparisonView(_View):
    """The comparison table as the screen renders it, stored as JSON on the conversation.

    A refresh shows exactly what the last change calculated, and nothing is recalculated on
    the way out (Req 8.15, 11.10).
    """

    today: TodayView
    alternatives: tuple[AlternativeView, ...]

    @classmethod
    def from_comparison(cls, comparison: Comparison, inputs: ComparisonInputs) -> Self:
        return cls(
            today=_today_view(comparison.today, inputs),
            alternatives=tuple(
                _alternative_view(position, result, inputs)
                for position, result in enumerate(comparison.alternatives)
            ),
        )


@dataclass(frozen=True)
class OpenQuestion:
    field: MissingField
    car_display_name: str | None
    car_key: CarKey | None


@dataclass(frozen=True)
class AgentStatus:
    running: bool = False


@dataclass(frozen=True)
class ConversationState:
    id: UUID
    messages: tuple[StoredMessage, ...]
    profile: EmployeeProfile
    alternatives: tuple[Alternative, ...]
    comparison: ComparisonView | None
    open_questions: tuple[OpenQuestion, ...]
    agent: AgentStatus


@dataclass(frozen=True)
class ProfileUpdate:
    """The outcome of a partial update: the stored profile and what became of each change."""

    profile: EmployeeProfile
    # The fields that were stored, in profile order.
    saved: tuple[str, ...]
    # The fields that were not, each with the values it allows, in profile order. A name that is
    # not a profile field comes last.
    rejected: tuple[FieldError, ...]


@dataclass(frozen=True)
class _State:
    """The part of a conversation that a change can edit, and the comparison depends on."""

    profile: EmployeeProfile
    alternatives: tuple[Alternative, ...]


# The (field, car) pairs of the questions sent to Transport_Rep, as ComparisonInputs wants them.
type _SentQuestions = frozenset[tuple[MissingField, CarKey | None]]


class ConversationService:
    def __init__(
        self, sessions: sessionmaker[Session], lookup: CarLookup, config: AppConfig
    ) -> None:
        self._sessions = sessions
        self._lookup = lookup
        self._config = config

    def create(self) -> UUID:
        with self._sessions.begin() as session:
            row = ConversationRow(
                created_at=utc_now(),
                employee_number=1000 + secrets.randbelow(9000),
                profile="{}",
                alternatives="[]",
            )
            session.add(row)
            session.flush()
            return row.id

    def state(self, conversation_id: UUID) -> ConversationState:
        with self._sessions() as session:
            # The screen polls this method, and the agent history, which it does not show,
            # is the largest column.
            row = session.get(
                ConversationRow, conversation_id, options=[defer(ConversationRow.agent_history)]
            )
            if row is None:
                raise ConversationNotFound

            # Each result is read in full before the next statement runs, because a SQL Server
            # connection that has unread rows refuses a second statement.
            messages = session.scalars(
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id)
                .order_by(MessageRow.id)
            ).all()
            questions = session.scalars(
                select(RepQuestionRow)
                .where(
                    RepQuestionRow.conversation_id == conversation_id,
                    RepQuestionRow.status == _OPEN,
                )
                .order_by(RepQuestionRow.created_at)
            ).all()
            return ConversationState(
                id=row.id,
                messages=tuple(_stored_message(message) for message in messages),
                profile=_stored_profile(row),
                alternatives=_stored_alternatives(row),
                comparison=None
                if row.comparison is None
                else ComparisonView.model_validate_json(row.comparison),
                open_questions=tuple(_open_question(question) for question in questions),
                agent=AgentStatus(running=_guard_held(row.agent_run_started_at)),
            )

    def append_message(self, conversation_id: UUID, message: NewMessage) -> None:
        with self._sessions.begin() as session:
            _require_conversation(session, conversation_id)
            _add_message(session, conversation_id, message)

    def update_profile(
        self, conversation_id: UUID, changes: Mapping[str, object]
    ) -> EmployeeProfile:
        """Store the changes, or raise a ProfileValidationError that lists every invalid field.

        Nothing is stored or recalculated when any change is invalid (Req 11.3). A None value
        clears its field.
        """

        def change(state: _State) -> tuple[_State, EmployeeProfile]:
            outcome = _apply_valid_changes(state.profile, changes)
            if outcome.rejected:
                raise ProfileValidationError(outcome.rejected)
            return replace(state, profile=outcome.profile), outcome.profile

        profile, _ = self._mutate(conversation_id, change)
        return profile

    def update_profile_partial(
        self, conversation_id: UUID, changes: Mapping[str, object]
    ) -> ProfileUpdate:
        """Store the valid changes and report the invalid ones with the values they allow.

        A rejected field keeps its stored value (Req 9.16).
        """

        def change(state: _State) -> tuple[_State, ProfileUpdate]:
            outcome = _apply_valid_changes(state.profile, changes)
            return replace(state, profile=outcome.profile), outcome

        outcome, _ = self._mutate(conversation_id, change)
        return outcome

    def set_alternatives(
        self, conversation_id: UUID, alternatives: Sequence[Alternative]
    ) -> ComparisonView:
        """Store the Alternative list, which sets the columns of the comparison, in order."""

        def change(state: _State) -> tuple[_State, None]:
            return replace(state, alternatives=tuple(alternatives)), None

        _, view = self._mutate(conversation_id, change)
        return view

    def replace_alternative(
        self, conversation_id: UUID, position: int, alternative: Alternative
    ) -> ConversationState:
        """Replace the Alternative in one column, raising AlternativeNotFound for a bad position."""

        def change(state: _State) -> tuple[_State, None]:
            if not 0 <= position < len(state.alternatives):
                raise AlternativeNotFound(f"no alternative at position {position}")
            stored = state.alternatives
            replaced = (*stored[:position], alternative, *stored[position + 1 :])
            return replace(state, alternatives=replaced), None

        self._mutate(conversation_id, change)
        return self.state(conversation_id)

    def transaction(self) -> AbstractContextManager[Session]:
        """Open a session in a transaction that commits at the end of the block, or rolls back.

        For the callers of recalculate, which store a change of their own in that transaction.
        """
        return self._sessions.begin()

    def recalculate(self, session: Session, conversation_id: UUID) -> ComparisonView:
        """Recalculate and store the comparison inside the caller's transaction.

        For a change that this service does not make but that moves the comparison, such as a
        Rep_Question that was just opened or answered. The caller commits, and gets a
        StaleDataError at the commit when a concurrent edit of the conversation got in first.
        """
        row = _load_row(session, conversation_id)
        state = _State(_stored_profile(row), _stored_alternatives(row))
        view = self._comparison_view(session, conversation_id, state)
        row.comparison = view.model_dump_json()
        # The version moves on even when the comparison is the same. A change to the profile that
        # read the conversation before this answer landed then fails its version check and is
        # applied again, instead of overwriting this comparison with one that lacks the answer.
        flag_modified(row, "comparison")
        return view

    def try_start_run(self, conversation_id: UUID) -> datetime | None:
        """Take the run guard of the conversation and return the time it was taken.

        None means that a run holds the guard, so this one must not start (Req 10.10). A guard
        older than RUN_GUARD_TTL belongs to a run that crashed, and is taken over. Pass the
        returned time to finish_run, which then releases this guard and no later one. An unknown
        conversation raises ConversationNotFound.

        This, finish_run and save_turn are plain UPDATEs that leave the version alone, so a run
        never makes a profile edit that is in flight stale.
        """
        now = utc_now()
        # One conditional statement, so that two requests cannot both take the guard.
        take = (
            update(ConversationRow)
            .where(
                ConversationRow.id == conversation_id,
                or_(
                    ConversationRow.agent_run_started_at.is_(None),
                    ConversationRow.agent_run_started_at < now - RUN_GUARD_TTL,
                ),
            )
            .values(agent_run_started_at=now)
        )
        with self._sessions.begin() as session:
            # On the connection, because that result type has the row count.
            taken = session.connection().execute(take).rowcount
            if not taken:
                # Only now is a held guard told apart from a conversation that does not exist.
                _require_conversation(session, conversation_id)
        return now if taken else None

    def finish_run(self, conversation_id: UUID, started_at: datetime) -> None:
        """Release the guard that try_start_run took at started_at.

        Safe in a finally block, and safe to call again: it changes nothing when the guard was
        released already or a later run took it over since, and nothing for an unknown
        conversation.
        """
        with self._sessions.begin() as session:
            session.connection().execute(
                update(ConversationRow)
                .where(
                    ConversationRow.id == conversation_id,
                    ConversationRow.agent_run_started_at == started_at,
                )
                .values(agent_run_started_at=None)
            )

    def load_history(self, conversation_id: UUID) -> bytes | None:
        """The agent history that the last save_turn stored, or None before the first turn.

        An unknown conversation raises ConversationNotFound.
        """
        with self._sessions() as session:
            found = session.execute(
                select(ConversationRow.agent_history).where(ConversationRow.id == conversation_id)
            ).one_or_none()
        if found is None:
            raise ConversationNotFound
        (history,) = found
        return None if history is None else history.encode()

    def save_turn(self, conversation_id: UUID, history: bytes, reply: str) -> None:
        """Store the agent history and append the advisor's reply, both or neither.

        The history is the serialized message list of the agent, and this service keeps it as
        it is. A blank reply raises ValueError and an unknown conversation raises
        ConversationNotFound, in both cases before anything is stored.
        """
        message = NewMessage(MessageRole.ADVISOR, reply)
        stored_history = history.decode()
        with self._sessions.begin() as session:
            _require_conversation(session, conversation_id)
            session.connection().execute(
                update(ConversationRow)
                .where(ConversationRow.id == conversation_id)
                .values(agent_history=stored_history)
            )
            _add_message(session, conversation_id, message)

    def _mutate[T](
        self, conversation_id: UUID, change: Callable[[_State], tuple[_State, T]]
    ) -> tuple[T, ComparisonView]:
        """Apply a change to the stored state and recalculate, in one transaction.

        The change runs again on freshly read data when a concurrent edit makes the first
        attempt stale, and ConversationConflict is raised if that attempt goes stale too.
        """

        def attempt() -> tuple[T, ComparisonView]:
            with self._sessions.begin() as session:
                row = _load_row(session, conversation_id)
                state, result = change(_State(_stored_profile(row), _stored_alternatives(row)))
                view = self._comparison_view(session, conversation_id, state)
                # All three columns are assigned last, so a single UPDATE with the version check
                # writes the state and the comparison that was calculated from it.
                row.profile = state.profile.model_dump_json(exclude_none=True)
                row.alternatives = _ALTERNATIVES.dump_json(list(state.alternatives)).decode()
                row.comparison = view.model_dump_json()
                return result, view

        for _ in range(_ATTEMPTS):
            try:
                return attempt()
            except StaleDataError:
                continue
        raise ConversationConflict("the conversation was changed by another request")

    def _comparison_view(
        self, session: Session, conversation_id: UUID, state: _State
    ) -> ComparisonView:
        records = self._lookup.get_many(_car_keys(state))
        questions, participations = _rep_inputs(session, conversation_id)
        inputs = ComparisonInputs(
            rules=self._config.rules,
            catalog=self._config.catalog,
            occupations=self._config.occupations,
            records=records,
            rep_participations=participations,
            rep_questions=questions,
        )
        comparison = compare(state.profile, state.alternatives, inputs)
        return ComparisonView.from_comparison(comparison, inputs)


def _require_conversation(session: Session, conversation_id: UUID) -> None:
    found = session.scalar(select(ConversationRow.id).where(ConversationRow.id == conversation_id))
    if found is None:
        raise ConversationNotFound


def _add_message(session: Session, conversation_id: UUID, message: NewMessage) -> None:
    session.add(
        MessageRow(
            conversation_id=conversation_id,
            role=message.role.value,
            text=message.text,
            notice_code=None if message.notice is None else message.notice.value,
            created_at=utc_now(),
        )
    )


def _stored_message(row: MessageRow) -> StoredMessage:
    return StoredMessage(
        id=row.id,
        role=MessageRole(row.role),
        text=row.text,
        notice=None if row.notice_code is None else NoticeCode(row.notice_code),
        created_at=as_utc(row.created_at),
    )


def _open_question(row: RepQuestionRow) -> OpenQuestion:
    return OpenQuestion(
        field=MissingField(row.missing_field),
        car_display_name=row.car_display_name,
        car_key=None if row.car_key == NO_CAR else CarKey.parse(row.car_key),
    )


def _guard_held(started_at: datetime | None) -> bool:
    # Exactly the guards that try_start_run does not take.
    return started_at is not None and started_at >= utc_now() - RUN_GUARD_TTL


def _load_row(session: Session, conversation_id: UUID) -> ConversationRow:
    # A change never touches the agent history, so it is not read.
    row = session.get(
        ConversationRow, conversation_id, options=[defer(ConversationRow.agent_history)]
    )
    if row is None:
        raise ConversationNotFound
    return row


def _stored_profile(row: ConversationRow) -> EmployeeProfile:
    return EmployeeProfile.model_validate_json(row.profile)


def _stored_alternatives(row: ConversationRow) -> tuple[Alternative, ...]:
    return tuple(_ALTERNATIVES.validate_json(row.alternatives))


def _apply_valid_changes(stored: EmployeeProfile, changes: Mapping[str, object]) -> ProfileUpdate:
    """Apply the changes that are valid to the stored profile and list the ones that are not.

    Both update paths use this, so they agree on which values are invalid. A name that is not a
    profile field is rejected like an invalid value, so that a misspelled field is never
    dropped without a word.
    """
    fields = EmployeeProfile.model_fields
    rejected = [
        FieldError(name, copy_he.UNKNOWN_PROFILE_FIELD) for name in changes if name not in fields
    ]
    accepted = {name: value for name, value in changes.items() if name in fields}
    stored_values = stored.model_dump(exclude_none=True)
    try:
        profile = validate_profile({**stored_values, **accepted})
    except ProfileValidationError as invalid:
        rejected = [*invalid.errors, *rejected]
        invalid_names = {error.field for error in invalid.errors}
        accepted = {name: value for name, value in accepted.items() if name not in invalid_names}
        profile = validate_profile({**stored_values, **accepted})
    return ProfileUpdate(
        profile=profile,
        saved=tuple(name for name in fields if name in accepted),
        rejected=tuple(rejected),
    )


def _car_keys(state: _State) -> list[CarKey]:
    """The cars whose dataset records the comparison needs: the Alternatives and the current car."""
    keys = [
        alternative.car_key
        for alternative in state.alternatives
        if isinstance(alternative, CarAlternative)
    ]
    if state.profile.current_car_key is not None:
        keys.append(state.profile.current_car_key)
    return keys


def _rep_inputs(
    session: Session, conversation_id: UUID
) -> tuple[_SentQuestions, dict[CarKey, RepParticipation]]:
    """Read the questions sent to Transport_Rep and the participations it answered.

    The latest answer for a car wins, because the rows come in the order they were answered.
    """
    rows = session.execute(
        select(
            RepQuestionRow.missing_field,
            RepQuestionRow.car_key,
            RepQuestionRow.status,
            RepQuestionRow.participation_amount,
            RepQuestionRow.participation_effective_date,
        )
        .where(RepQuestionRow.conversation_id == conversation_id)
        .order_by(RepQuestionRow.answered_at, RepQuestionRow.created_at, RepQuestionRow.id)
    )
    questions: set[tuple[MissingField, CarKey | None]] = set()
    participations: dict[CarKey, RepParticipation] = {}
    for field_name, car_text, status, amount, effective_date in rows:
        field = MissingField(field_name)
        # The key is kept only for the fields of a car, as in the missing items of the engine,
        # or a question about the employee would never be found as sent.
        car_key = CarKey.parse(car_text) if field in CAR_FIELDS and car_text != NO_CAR else None
        questions.add((field, car_key))
        if (
            status == _ANSWERED
            and field is MissingField.NEW_CAR_PARTICIPATION
            and car_key is not None
            and amount is not None
            and effective_date is not None
        ):
            participations[car_key] = RepParticipation(amount=amount, effective_date=effective_date)
    return frozenset(questions), participations


def _today_view(today: TodayResult, inputs: ComparisonInputs) -> TodayView:
    return TodayView(
        status=today.status,
        car=None if today.car_key is None else _car_view(today.car_key, inputs),
        participation=_shown(today.participation),
        usage_value=_shown(today.usage_value),
        ev_refund=_shown(today.ev_refund),
        car_maintenance=_shown(today.car_maintenance),
        net_pay=_shown(today.net_pay),
        missing=_missing_views(today.missing),
    )


def _alternative_view(
    position: int, result: AlternativeResult, inputs: ComparisonInputs
) -> AlternativeView:
    alternative = result.alternative
    eligibility = result.eligibility
    breakdown = result.breakdown
    return AlternativeView(
        position=position,
        status=result.status,
        car=_car_view(alternative.car_key, inputs)
        if isinstance(alternative, CarAlternative)
        else None,
        eligibility=EligibilityView(
            status=eligibility.status,
            reason=eligibility.reason,
            is_electric=eligibility.is_electric,
        ),
        car_cost=_shown(result.car_cost),
        car_budget=_shown(result.car_budget),
        participation=_shown(result.participation),
        participation_effective_date=result.participation_effective_date,
        employer_gap_share=_shown(result.employer_gap_share),
        employee_gap_share=_shown(result.employee_gap_share),
        usage_value=_shown(result.usage_value),
        ev_refund=_shown(result.ev_refund),
        car_maintenance=_shown(result.car_maintenance),
        unused_budget=_shown(result.unused_budget),
        net_pay=_shown(result.net_pay),
        difference=_shown(result.difference),
        breakdown=None
        if breakdown is None
        else BreakdownView(
            usage_value=breakdown.usage_value.shown,
            ev_refund=breakdown.ev_refund.shown,
            participation=breakdown.participation.shown,
            car_maintenance=breakdown.car_maintenance.shown,
        ),
        explanation=result.explanation,
        missing=_missing_views(result.missing),
    )


def _car_view(key: CarKey, inputs: ComparisonInputs) -> CarView:
    car = inputs.catalog.find(key)
    if car is not None:
        return CarView(car_key=key, display_name=car.display_name, group=car.group)
    # Most current cars are not in the catalog, and their column needs a name all the same.
    record = inputs.records.get(key)
    name = None if record is None else f"{record.manufacturer_name} {record.trade_name}".strip()
    return CarView(car_key=key, display_name=name, group=None)


def _missing_views(items: Iterable[MissingItem]) -> tuple[MissingView, ...]:
    return tuple(
        MissingView(
            field=item.field,
            label=copy_he.MISSING_FIELD_LABELS[item.field],
            sent_to_rep=item.sent_to_rep,
        )
        for item in items
    )


def _shown(value: Amount | None) -> int | None:
    return None if value is None else value.shown
