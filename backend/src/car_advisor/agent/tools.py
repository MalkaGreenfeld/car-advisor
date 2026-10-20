import functools
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, Concatenate, Final, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    JsonValue,
    SkipValidation,
    ValidationError,
    WithJsonSchema,
)
from pydantic_ai import Agent, ModelRetry, RunContext
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from car_advisor.conversations.profile import FieldError
from car_advisor.conversations.rep_questions import RepField, open_or_get
from car_advisor.conversations.service import (
    ComparisonView,
    ConversationConflict,
    ConversationService,
)
from car_advisor.dataset.lookup import (
    Candidates,
    CarCandidate,
    CarLookup,
    Found,
    KeyParts,
    LookupFailed,
    NotFound,
    Rejected,
)
from car_advisor.engine import copy_he, eligibility
from car_advisor.engine.config import CatalogCar
from car_advisor.engine.types import (
    Alternative,
    CarAlternative,
    CarKey,
    CarRecord,
    CurrentCarDescription,
    Group,
    MissingField,
    Money,
    PayComponent,
    Percent,
    Powertrain,
    WaiverAlternative,
)
from car_advisor.logs import log_tool_call
from car_advisor.rules.loader import AppConfig

# At most this many candidates go to the model. A search such as "A" in the busiest registration
# year matches thousands of records, which no chat can show and no turn can afford to read.
MAX_CANDIDATES: Final = 20
# A change is applied once, and once more on freshly read data when a concurrent edit got in first.
_ATTEMPTS: Final = 2
_CURRENT_CAR_KEY: Final = "current_car_key"


@dataclass(frozen=True)
class AdvisorDeps:
    """What the tools work on: their conversation, and the services that serve it.

    The services keep no state apart from their session factory, so the threads that run the tools
    can share them.
    """

    conversation_id: UUID
    conversations: ConversationService
    lookup: CarLookup
    config: AppConfig


class _Result(BaseModel, frozen=True):
    """What a tool returns.

    It holds codes, whole-shekel integers and Hebrew labels, and never an exact Decimal, so the
    model can only repeat what the engine, the dataset or the stored state said.
    """


class FailedResult(_Result):
    """A result that tells the model its action failed. The log line of the call says `ok=false`."""


class ToolError(FailedResult):
    """The result of a tool that failed, naming the action in Hebrew (Req 9.17).

    It holds no number, so it adds nothing to what the model may say.
    """

    error: str
    action_label: str


class FieldLabel(_Result):
    field: MissingField
    label: str

    @classmethod
    def of(cls, field: MissingField) -> Self:
        return cls(field=field, label=copy_he.MISSING_FIELD_LABELS[field])


class CarDetails(_Result):
    """What tells one car from the others (Req 2.6)."""

    car_key: CarKey
    manufacturer: str
    trade_name: str
    model_code: str
    model_description: str
    vehicle_type_code: str
    engine_cc: int
    powertrain: Powertrain
    powertrain_label: str
    list_price: int


class CarRecordDetails(CarDetails):
    """A car read by its key, with the Usage_Value of the dataset (Req 2.1, 2.2)."""

    adjusted_price: int
    usage_value: int


class FoundCar(_Result):
    outcome: Literal["found"] = "found"
    car: CarRecordDetails


class CarNotFound(_Result):
    """No car has the key, and no other car stands in for it (Req 2.3)."""

    outcome: Literal["not_found"] = "not_found"
    car_key: CarKey


class LookupRejected(_Result):
    """The request lacked inputs, each named by the tool parameter that carries it (Req 2.4)."""

    outcome: Literal["rejected"] = "rejected"
    missing: list[str]


class CarCandidates(_Result):
    outcome: Literal["candidates"] = "candidates"
    match_count: int
    candidates: list[CarDetails]
    # More than one match: the employee has to pick one of them (Req 2.7).
    selection_required: bool
    none_found: bool
    # There are more matches than candidates here, so the search has to be narrowed.
    truncated: bool


type LookupToolResult = FoundCar | CarNotFound | CarCandidates | LookupRejected


class CatalogCarItem(_Result):
    car_key: CarKey
    display_name: str
    group_label: str
    family_track: bool
    # From the dataset record and not from the name: a car is electric by its Powertrain.
    powertrain: Powertrain
    powertrain_label: str


class CatalogToolResult(_Result):
    cars: list[CatalogCarItem]
    # The eligibility group while none is stored, and then there are no cars (Req 4.17).
    missing: FieldLabel | None


class EligibilityToolResult(_Result):
    status: Literal["eligible", "not_eligible", "missing"]
    reason: str
    missing: list[FieldLabel]
    is_electric: bool | None


class RejectedField(_Result):
    field: str
    # The values the field allows, in Hebrew.
    allowed: str


class ProfileUpdateResult(_Result):
    # The stored value of every field that was saved, so the bot may repeat them.
    saved: dict[str, JsonValue]
    rejected: list[RejectedField]


class RepSent(_Result):
    sent: Literal[True] = True
    # For the application only: the digits of an ID would count as numbers that a tool returned.
    question_id: UUID = Field(exclude=True)
    # False when the question was open already (Req 10.4).
    created: bool
    # The sentence to tell the employee as it is (Req 10.3).
    employee_notice: str


class RepNotSent(FailedResult):
    """The question could not be stored, so it did not reach Transport_Rep (Req 10.16)."""

    sent: Literal[False] = False
    employee_notice: str


type RepToolResult = RepSent | RepNotSent

CarKeyArg = Annotated[
    CarKey,
    WithJsonSchema(
        {
            "type": "string",
            "description": (
                "Official_Key as tax_year-registration_year-vehicle_type-manufacturer-model, "
                "for example 2026-2020-1-01229-00001, exactly as a tool returned it."
            ),
        }
    ),
]


class CarAlternativeArg(BaseModel, extra="forbid"):
    kind: Literal["car"]
    car_key: CarKeyArg


class WaiverAlternativeArg(BaseModel, extra="forbid"):
    kind: Literal["waiver"]


AlternativeArg = Annotated[CarAlternativeArg | WaiverAlternativeArg, Field(discriminator="kind")]


# The values are not validated here but when they are saved, so that one invalid value does not
# keep the valid ones of the same message from being stored (Req 9.16). The annotations are those
# of EmployeeProfile, for the schema that the model reads.
class ProfileChanges(BaseModel, extra="forbid", use_attribute_docstrings=True):
    """Employee_Profile fields to save. Include only the fields the employee gave."""

    display_name: SkipValidation[str | None] = None
    """The employee's name, only if they gave it."""
    eligibility_group: SkipValidation[Group | None] = None
    """Eligibility group: A, B, C or D. The Hebrew letters alef, bet, gimel and dalet are A to D."""
    base_salary: SkipValidation[Money | None] = None
    """Monthly base salary in shekels."""
    pay_components: SkipValidation[list[PayComponent] | None] = None
    """Additional pay: commissions and bonuses with a label and a monthly amount in shekels. An
    empty list means that the employee has none."""
    pension_rate_percent: SkipValidation[Percent | None] = None
    """The employee's pension contribution, as a percentage of salary from 0 to 100."""
    study_fund_rate_percent: SkipValidation[Percent | None] = None
    """The employee's study fund contribution, as a percentage of salary from 0 to 100."""
    credit_points: SkipValidation[Annotated[Decimal, Field(ge=0)] | None] = None
    """Tax credit points, 0 or more. They may have decimals."""
    occupation: SkipValidation[str | None] = None
    """The employee's occupation: the name of the table's entry when the employee's words clearly
    name one, otherwise as the employee said it."""
    children_under_23: SkipValidation[Annotated[int, Field(ge=0)] | None] = None
    """The number of children up to age 23, a whole number from 0."""
    has_current_car: SkipValidation[bool | None] = None
    """True when the employee has a company car today, false when not."""
    current_car: SkipValidation[CurrentCarDescription | None] = None
    """The current car as the employee described it. It does not identify the car."""
    current_car_key: SkipValidation[CarKeyArg | None] = None
    """Official_Key of the current car. Only after the employee confirmed one candidate that
    lookup_car returned, and it is saved only if the dataset has that key."""
    current_car_participation: SkipValidation[Money | None] = None
    """The monthly amount in shekels that the employee pays for the current car."""


def logged_tool[**P, R](
    tool: Callable[Concatenate[RunContext[AdvisorDeps], P], R],
) -> Callable[Concatenate[RunContext[AdvisorDeps], P], R | ToolError]:
    """Wrap a tool so that each call writes one log line, and a failure becomes the error shape.

    An exception that the tool raises becomes `{"error": "<tool>_failed", "action_label": ...}`
    (Req 9.17). The log line holds the type of the exception and never its message, which can quote
    a salary. A ModelRetry asks the model to correct its call, so it goes on to the agent. A result
    of the FailedResult kind counts as a failure in the log as well (Req 14.10).
    """
    name = tool.__name__
    # Looked up now, so a tool without a Hebrew label fails when the module loads.
    failure = ToolError(error=f"{name}_failed", action_label=copy_he.TOOL_ACTION_LABELS[name])

    @functools.wraps(tool)
    def wrapper(
        ctx: RunContext[AdvisorDeps], /, *args: P.args, **kwargs: P.kwargs
    ) -> R | ToolError:
        started = time.perf_counter()

        def log(*, ok: bool, error_type: str | None = None) -> None:
            log_tool_call(
                name,
                ok=ok,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                conversation_id=ctx.deps.conversation_id,
                error_type=error_type,
            )

        try:
            result = tool(ctx, *args, **kwargs)
        except Exception as error:
            log(ok=False, error_type=type(error).__name__)
            if isinstance(error, ModelRetry):
                raise
            return failure
        log(ok=not isinstance(result, FailedResult))
        return result

    return wrapper


class LookupUnavailable(Exception):
    """Car_Lookup could not read the Usage_Value_Dataset (Req 2.14)."""


def _alternative(arg: CarAlternativeArg | WaiverAlternativeArg) -> Alternative:
    if isinstance(arg, CarAlternativeArg):
        return CarAlternative(car_key=arg.car_key)
    return WaiverAlternative()


def _given(code: str | None) -> bool:
    return code is not None and bool(code.strip())


def _candidate_details(candidate: CarCandidate) -> CarDetails:
    return CarDetails(
        car_key=candidate.key,
        manufacturer=candidate.manufacturer_name,
        trade_name=candidate.trade_name,
        model_code=candidate.model_code,
        model_description=candidate.model_description,
        vehicle_type_code=candidate.vehicle_type_code,
        engine_cc=candidate.engine_cc,
        powertrain=candidate.powertrain,
        powertrain_label=copy_he.POWERTRAIN_LABELS[candidate.powertrain],
        list_price=candidate.list_price,
    )


def _record_details(record: CarRecord) -> CarRecordDetails:
    return CarRecordDetails(
        car_key=record.key,
        manufacturer=record.manufacturer_name,
        trade_name=record.trade_name,
        model_code=record.key.model_code,
        model_description=record.model_description,
        vehicle_type_code=record.key.vehicle_type_code,
        engine_cc=record.engine_cc,
        powertrain=record.powertrain,
        powertrain_label=copy_he.POWERTRAIN_LABELS[record.powertrain],
        list_price=record.list_price,
        adjusted_price=record.adjusted_price,
        usage_value=record.usage_value,
    )


def _lookup_result(
    outcome: Found | NotFound | Candidates | Rejected | LookupFailed,
) -> LookupToolResult:
    match outcome:
        case Found(record):
            return FoundCar(car=_record_details(record))
        case NotFound(key):
            return CarNotFound(car_key=key)
        case Candidates(items, selection_required, none_found):
            shown = items[:MAX_CANDIDATES]
            return CarCandidates(
                match_count=len(items),
                candidates=[_candidate_details(item) for item in shown],
                selection_required=selection_required,
                none_found=none_found,
                truncated=len(shown) < len(items),
            )
        case Rejected(missing):
            # Car_Lookup calls the search text "text", and the tool calls it search_text.
            return LookupRejected(
                missing=["search_text" if name == "text" else name for name in missing]
            )
        case LookupFailed(reason):
            raise LookupUnavailable(reason)


@logged_tool
def lookup_car(
    ctx: RunContext[AdvisorDeps],
    registration_year: int | None = None,
    search_text: str | None = None,
    vehicle_type_code: str | None = None,
    manufacturer_code: str | None = None,
    model_code: str | None = None,
) -> LookupToolResult:
    """Find cars in the official Usage_Value dataset and read their details.

    With the vehicle type, manufacturer and model codes it reads the one exact car, which also
    needs registration_year. Without codes it lists the candidate cars registered in
    registration_year whose manufacturer or trade name contains search_text. A car is identified
    only when the employee confirms one complete candidate, even if the search returns a single
    one, so show the details that tell the candidates apart and ask. When truncated is true, ask
    the employee for more detail and search again. Never guess a code.

    Args:
        registration_year: The year the car was first registered.
        search_text: Part of the manufacturer or trade name, used when no code is given.
        vehicle_type_code: The vehicle type code, as text with its leading zeros.
        manufacturer_code: The manufacturer code, as text with its leading zeros.
        model_code: The model code, as text with its leading zeros.
    """
    lookup = ctx.deps.lookup
    if any(_given(code) for code in (vehicle_type_code, manufacturer_code, model_code)):
        parts = KeyParts(
            lookup.tax_year, registration_year, vehicle_type_code, manufacturer_code, model_code
        )
        return _lookup_result(lookup.get(parts))
    return _lookup_result(lookup.search(registration_year, search_text))


@logged_tool
def list_catalog_cars(ctx: RunContext[AdvisorDeps]) -> CatalogToolResult:
    """List the cars of the Car_Catalog that the employee may choose from.

    These are the cars of the employee's own eligibility group and of the groups below it, taken
    from the stored profile. Without a stored group there are no cars and the missing field is
    named, so ask the employee for the group first. Each car comes with the key to pass to the
    other tools. Only the Powertrain says which cars are electric.
    """
    deps = ctx.deps
    group = deps.conversations.state(deps.conversation_id).profile.eligibility_group
    if group is None:
        return CatalogToolResult(cars=[], missing=FieldLabel.of(MissingField.ELIGIBILITY_GROUP))
    cars = deps.config.catalog.cars_for(group, deps.config.rules.groups.order)
    records = deps.lookup.get_many(car.official_key for car in cars)
    return CatalogToolResult(
        cars=[_catalog_item(car, records[car.official_key]) for car in cars], missing=None
    )


def _catalog_item(car: CatalogCar, record: CarRecord) -> CatalogCarItem:
    return CatalogCarItem(
        car_key=car.official_key,
        display_name=car.display_name,
        group_label=copy_he.GROUP_LETTERS[car.group],
        family_track=car.family_track,
        powertrain=record.powertrain,
        powertrain_label=copy_he.POWERTRAIN_LABELS[record.powertrain],
    )


@logged_tool
def check_eligibility(
    ctx: RunContext[AdvisorDeps], alternative: AlternativeArg
) -> EligibilityToolResult:
    """Check whether the employee may take an alternative, on the stored profile.

    The alternative is a catalog car or giving up the car (a waiver). The status and the reason
    are the only decision you may state, and only after this tool returned them. With the status
    "missing" there is no decision yet: the missing fields are listed, so ask the employee for
    them, or use ask_rep for what only the representative can answer.

    Args:
        alternative: A car, as {"kind": "car", "car_key": ...}, or {"kind": "waiver"}.
    """
    deps = ctx.deps
    chosen = _alternative(alternative)
    profile = deps.conversations.state(deps.conversation_id).profile
    record = None
    if isinstance(chosen, CarAlternative):
        record = deps.lookup.get_many([chosen.car_key]).get(chosen.car_key)
    result = eligibility.check_eligibility(
        profile, chosen, deps.config.rules, deps.config.catalog, deps.config.occupations, record
    )
    return EligibilityToolResult(
        status=result.status,
        reason=result.reason,
        missing=[FieldLabel.of(field) for field in result.missing_fields],
        is_electric=result.is_electric,
    )


@logged_tool
def compare_alternatives(
    ctx: RunContext[AdvisorDeps], alternatives: list[AlternativeArg]
) -> ComparisonView:
    """Compare the alternatives with the employee's situation today.

    The list replaces the stored alternatives and becomes the columns of the comparison table, so
    pass every alternative the employee wants to see, in column order. It returns the comparison
    as the employee sees it in the table. Every amount is in whole shekels and is the only figure
    you may state; never calculate or round one. A column with the status "pending" lists what is
    missing and has no amounts, and a "not_eligible" column has only the reason.

    Args:
        alternatives: Each as {"kind": "car", "car_key": ...} or {"kind": "waiver"}.
    """
    deps = ctx.deps
    return deps.conversations.set_alternatives(
        deps.conversation_id, [_alternative(alternative) for alternative in alternatives]
    )


def _stored_occupation(deps: AdvisorDeps) -> str:
    occupation = deps.conversations.state(deps.conversation_id).profile.occupation
    return (occupation or "").strip()


def _store_question(
    deps: AdvisorDeps, field: RepField, car: CatalogCar | None
) -> tuple[UUID, bool]:
    """Open the question, or get the one that is open, in a transaction of its own.

    A concurrent edit of the conversation makes the commit stale and rolls the new question back
    with its comparison, so the whole call is made again once (see open_or_get).
    """
    for _ in range(_ATTEMPTS):
        try:
            with deps.conversations.transaction() as session:
                question, created = open_or_get(
                    deps.conversations, session, deps.conversation_id, field, car
                )
                # Read before the commit, which expires the row.
                return question.id, created
        except StaleDataError:
            continue
    raise ConversationConflict("the conversation was changed by another request")


@logged_tool
def ask_rep(
    ctx: RunContext[AdvisorDeps], field: RepField, car_key: CarKeyArg | None = None
) -> RepToolResult:
    """Ask the transport representative for what only the representative can answer.

    Use it when the employee says they do not know the information, and for an occupation that is
    not in the occupations table. Ask the employee first for everything the employee can answer.
    Calling it again for the same field and car returns the question that is open, so repeating it
    is safe. Tell the employee the employee_notice sentence as it is, then show the comparison
    that compare_alternatives returns. Never guess the missing value.

    Args:
        field: What is missing. new_car_participation is the monthly participation in a new car,
            and needs car_key. occupation asks whether the stored occupation may give up the car.
            current_car asks for help to identify the current car. current_car_participation is
            the monthly participation in the current car.
        car_key: The catalog car that new_car_participation is about. Not used for other fields.
    """
    deps = ctx.deps
    car = None if car_key is None else deps.config.catalog.find(car_key)
    if field is RepField.NEW_CAR_PARTICIPATION and car is None:
        raise ModelRetry(
            "new_car_participation needs the car_key of a Car_Catalog car, "
            "as list_catalog_cars returns it."
        )
    if field is RepField.OCCUPATION and not _stored_occupation(deps):
        raise ModelRetry(
            "The occupation is not stored. Ask the employee for it and save it with "
            "update_profile before asking the representative."
        )
    label = copy_he.MISSING_FIELD_LABELS[field.missing_field]
    try:
        question_id, created = _store_question(deps, field, car)
    except (SQLAlchemyError, ConversationConflict):
        return RepNotSent(employee_notice=copy_he.rep_question_not_sent(label))
    return RepSent(
        question_id=question_id, created=created, employee_notice=copy_he.rep_question_sent(label)
    )


def _given_fields(changes: ProfileChanges) -> dict[str, object]:
    values = {name: getattr(changes, name) for name in changes.model_fields_set}
    # A null is the model filling in a field it has nothing for, not a request to clear the field.
    return {name: value for name, value in values.items() if value is not None}


def _is_in_dataset(lookup: CarLookup, value: object) -> bool:
    try:
        key = CarKey.model_validate(value)
    except ValidationError:
        return False
    return key in lookup.get_many([key])


@logged_tool
def update_profile(ctx: RunContext[AdvisorDeps], changes: ProfileChanges) -> ProfileUpdateResult:
    """Save the profile fields that the employee gave, including corrections of stored values.

    Call it in the turn in which the employee gives a value, before you answer, with all the fields
    of the message at once. A field left out stays as it is, and a null value is ignored. A value
    that is not allowed is not saved and comes back in rejected with the values the field allows:
    ask the employee for that field again, and do not say it was saved. The saved fields come back
    with their stored values, which you may repeat. The comparison table updates by itself.
    """
    deps = ctx.deps
    requested = _given_fields(changes)
    unconfirmed: list[FieldError] = []
    # The key marks the car as identified, so it is saved only for a car the dataset has (Req 2.9).
    if _CURRENT_CAR_KEY in requested and not _is_in_dataset(
        deps.lookup, requested[_CURRENT_CAR_KEY]
    ):
        del requested[_CURRENT_CAR_KEY]
        unconfirmed.append(
            FieldError(_CURRENT_CAR_KEY, copy_he.PROFILE_FIELD_RANGES[_CURRENT_CAR_KEY])
        )
    update = deps.conversations.update_profile_partial(deps.conversation_id, requested)
    stored = update.profile.model_dump(mode="json")
    return ProfileUpdateResult(
        saved={name: stored[name] for name in update.saved},
        rejected=[
            RejectedField(field=error.field, allowed=error.message)
            for error in (*update.rejected, *unconfirmed)
        ],
    )


TOOLS: Final = (
    lookup_car,
    list_catalog_cars,
    check_eligibility,
    compare_alternatives,
    ask_rep,
    update_profile,
)


def register_tools(agent: Agent[AdvisorDeps, Any]) -> None:
    """Register the six tools on the agent.

    They run one at a time, in the order the model called them. They read and write the one stored
    conversation, and the calls of one model response otherwise run concurrently, so a check could
    read the profile before the update that came first in the same response.
    """
    for tool in TOOLS:
        agent.tool(tool, sequential=True)
