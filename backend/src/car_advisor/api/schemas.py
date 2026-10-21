"""The bodies of the HTTP API.

The Angular client mirrors these models by hand in api-models.ts, so a rename or a new field here
is a change there too.
"""

from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    WithJsonSchema,
    model_validator,
)

from car_advisor.conversations.profile import FieldError
from car_advisor.conversations.rep_questions import RepQuestionView
from car_advisor.conversations.service import (
    ComparisonView,
    ConversationState,
    OpenQuestion,
    StoredMessage,
)
from car_advisor.dataset.lookup import Candidates, CarCandidate, UsageValueMismatch
from car_advisor.engine.config import CatalogCar
from car_advisor.engine.types import Alternative, CarKey, EmployeeProfile, Group, Money, Powertrain

_MESSAGE_MAX_LENGTH: Final = 2000
# The sizes of the columns that store an answer of Transport_Rep.
_ANSWER_TEXT_MAX_LENGTH: Final = 2000
_ANSWER_NOTE_MAX_LENGTH: Final = 500
_ANSWER_CHOICE_MAX_LENGTH: Final = 200


def _not_blank(text: str) -> str:
    if not text.strip():
        raise ValueError("must not be blank")
    return text


_NonBlank = Annotated[str, AfterValidator(_not_blank)]


def _only_text(value: object) -> object:
    # CarKey also reads its five parts as a mapping, but the API carries the key as one text.
    if not isinstance(value, str):
        raise ValueError("must be the Official_Key as text, like 2026-2020-1-01229-00001")
    return value


_CarKeyText = Annotated[CarKey, BeforeValidator(_only_text), WithJsonSchema({"type": "string"})]


class _Request(BaseModel, extra="forbid", frozen=True):
    """Base of the request bodies: a field the API does not know is a 422, never dropped."""


class _Response(BaseModel, frozen=True):
    """Base of the response bodies."""


class MessageRequest(_Request):
    # The text is stored and passed on as typed, so only its length and blankness are checked.
    text: Annotated[_NonBlank, Field(min_length=1, max_length=_MESSAGE_MAX_LENGTH)]


class ProfileChangesRequest(_Request):
    # A free mapping on purpose: the service rejects an unknown name and an invalid value field
    # by field, each with a Hebrew message, so this body cannot check either.
    changes: dict[str, Any]


class AlternativeRequest(_Request):
    """Replaces the car of one column. The car picker can choose a car and nothing else."""

    kind: Literal["car"]
    car_key: _CarKeyText


class ParticipationRequest(_Request):
    # Stored exactly as given (Req 6.1), so it must fit the DECIMAL(12,2) column without rounding.
    amount: Annotated[Money, Field(max_digits=12, decimal_places=2)]
    effective_date: date
    note: Annotated[str, Field(max_length=_ANSWER_NOTE_MAX_LENGTH)] | None = None


class RepAnswerRequest(_Request):
    """The answer of Transport_Rep: a participation, a choice, or a text (Req 10.5, 10.6)."""

    participation: ParticipationRequest | None = None
    # The table name of an occupation question, or the group of an eligibility group question.
    choice: Annotated[_NonBlank, Field(max_length=_ANSWER_CHOICE_MAX_LENGTH)] | None = None
    text: Annotated[_NonBlank, Field(max_length=_ANSWER_TEXT_MAX_LENGTH)] | None = None

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> Self:
        given = [part for part in (self.participation, self.choice, self.text) if part is not None]
        if len(given) != 1:
            raise ValueError("an answer holds exactly one of a participation, a choice or a text")
        return self


class AgentView(_Response):
    # False when no API key is set, so the bot cannot answer (Req 14.5, 9.15).
    available: bool
    # True while a run holds the guard of the conversation, and the screen keeps polling.
    running: bool


class EmployeeGroupView(_Response):
    # Both null while the occupation is missing or unlisted and Transport_Rep set no group.
    group: Group | None
    source: Literal["occupation", "representative"] | None


class ConversationStateResponse(_Response):
    """The conversation as the Employee_Screen renders it."""

    id: UUID
    messages: tuple[StoredMessage, ...]
    profile: EmployeeProfile
    # The group in use and its source, which the panel shows read-only (Req 11.2).
    employee_group: EmployeeGroupView
    alternatives: tuple[Alternative, ...]
    comparison: ComparisonView | None
    open_questions: tuple[OpenQuestion, ...]
    agent: AgentView

    @classmethod
    def from_state(cls, state: ConversationState, *, agent_available: bool) -> Self:
        # The service cannot know whether an agent exists, so the caller passes it in.
        return cls(
            id=state.id,
            messages=state.messages,
            profile=state.profile,
            employee_group=EmployeeGroupView(
                group=state.employee_group.group, source=state.employee_group.source
            ),
            alternatives=state.alternatives,
            comparison=state.comparison,
            open_questions=state.open_questions,
            agent=AgentView(available=agent_available, running=state.agent.running),
        )


class CatalogCarView(_Response):
    car_key: CarKey
    display_name: str
    group: Group

    @classmethod
    def from_car(cls, car: CatalogCar) -> Self:
        return cls(car_key=car.official_key, display_name=car.display_name, group=car.group)


class CatalogResponse(_Response):
    cars: tuple[CatalogCarView, ...]
    # Names the profile field to fill in when the list is empty for lack of it: the group follows
    # from the occupation (Req 4.17).
    missing_field: Literal["occupation"] | None = None


class OccupationsResponse(_Response):
    # The names of Occupations_Table, as the My Details panel suggests them (Req 11.19).
    names: tuple[str, ...]


class CarCandidateView(_Response):
    """A car that a search found, with the details of Req 2.6."""

    car_key: CarKey
    manufacturer_name: str
    trade_name: str
    model_code: str
    model_description: str
    vehicle_type_code: str
    engine_cc: int
    powertrain: Powertrain
    list_price: int

    @classmethod
    def from_candidate(cls, candidate: CarCandidate) -> Self:
        return cls(
            car_key=candidate.key,
            manufacturer_name=candidate.manufacturer_name,
            trade_name=candidate.trade_name,
            model_code=candidate.model_code,
            model_description=candidate.model_description,
            vehicle_type_code=candidate.vehicle_type_code,
            engine_cc=candidate.engine_cc,
            powertrain=candidate.powertrain,
            list_price=candidate.list_price,
        )


class CarSearchResponse(_Response):
    candidates: tuple[CarCandidateView, ...]
    selection_required: bool
    none_found: bool

    @classmethod
    def from_candidates(cls, found: Candidates) -> Self:
        return cls(
            candidates=tuple(CarCandidateView.from_candidate(item) for item in found.items),
            selection_required=found.selection_required,
            none_found=found.none_found,
        )


class RepQuestionsResponse(_Response):
    # RepQuestionView holds the fields of Req 12.3 and the answer, and no conversation ID.
    questions: tuple[RepQuestionView, ...]


class CatalogWarning(_Response):
    """A catalog car whose usage value differs from the dataset's; the dataset's is used."""

    display_name: str
    car_key: CarKey
    catalog_usage_value: int
    dataset_usage_value: int

    @classmethod
    def from_mismatch(cls, mismatch: UsageValueMismatch) -> Self:
        return cls(
            display_name=mismatch.display_name,
            car_key=mismatch.car_key,
            catalog_usage_value=mismatch.catalog_usage_value,
            dataset_usage_value=mismatch.dataset_usage_value,
        )


class Health(_Response):
    database_reachable: bool
    dataset_loaded: bool
    tax_year: int
    # 0 while the dataset cannot be read.
    record_count: Annotated[int, Field(ge=0)]
    # Found at startup; they never change the status (Req 4.19).
    catalog_warnings: tuple[CatalogWarning, ...] = ()


class ErrorCode(StrEnum):
    """The codes of the design's API and error tables, and no others."""

    CONVERSATION_NOT_FOUND = "conversation_not_found"
    AGENT_BUSY = "agent_busy"
    CONFLICT = "conflict"
    DATABASE_UNAVAILABLE = "database_unavailable"
    INVALID_FIELDS = "invalid_fields"
    MISSING_INPUTS = "missing_inputs"
    LOOKUP_FAILED = "lookup_failed"
    INVALID_ANSWER = "invalid_answer"
    ALREADY_ANSWERED = "already_answered"
    INVALID_FILE = "invalid_file"
    NOTHING_DETECTED = "nothing_detected"
    READER_FAILED = "reader_failed"
    READER_TIMEOUT = "reader_timeout"


def _is_absent(value: object) -> bool:
    return value is None


class ErrorBody(_Response):
    code: ErrorCode
    # Left out of the JSON, not null, when no field is to blame.
    fields: tuple[FieldError, ...] | None = Field(default=None, exclude_if=_is_absent)
