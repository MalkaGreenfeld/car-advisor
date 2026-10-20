import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic_ai import Agent, ModelRetry, RunContext, models
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.test import TestModel as StubModel
from pydantic_ai.usage import RunUsage
from sqlalchemy import Engine, create_engine, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.agent import tools
from car_advisor.agent.number_guard import numbers_in, tool_numbers
from car_advisor.agent.tools import (
    MAX_CANDIDATES,
    TOOLS,
    AdvisorDeps,
    CarAlternativeArg,
    CarCandidates,
    CarNotFound,
    CarRecordDetails,
    CatalogToolResult,
    EligibilityToolResult,
    FieldLabel,
    FoundCar,
    LookupRejected,
    ProfileChanges,
    ProfileUpdateResult,
    RejectedField,
    RepNotSent,
    RepSent,
    ToolError,
    WaiverAlternativeArg,
    ask_rep,
    check_eligibility,
    compare_alternatives,
    list_catalog_cars,
    lookup_car,
    register_tools,
    update_profile,
)
from car_advisor.conversations.models import ConversationRow, RepQuestionRow
from car_advisor.conversations.rep_questions import RepField, list_for_rep, open_or_get
from car_advisor.conversations.service import ConversationService, ConversationState
from car_advisor.dataset.lookup import Candidates, CarLookup
from car_advisor.engine import copy_he
from car_advisor.engine.config import CatalogCar
from car_advisor.engine.types import (
    CarAlternative,
    CarKey,
    EmployeeProfile,
    Group,
    MissingField,
    Powertrain,
    WaiverAlternative,
)
from car_advisor.logs import JsonLinesFormatter
from car_advisor.rules.loader import AppConfig

LOGGER = "car_advisor.logs"

# Cars of the real dataset, and what the mock catalog says about them.
CURRENT_KEY = CarKey.parse("2026-2023-1-01229-00177")  # a 2023 Tesla Model 3, not in the catalog
ATTO_3_KEY = CarKey.parse("2026-2025-1-01014-00003")  # group C, electric
TESLA_KEY = CarKey.parse("2026-2025-1-01327-00730")  # group D, electric
NIRO_KEY = CarKey.parse("2026-2025-1-00885-00619")  # group B, hybrid
PICANTO_KEY = CarKey.parse("2026-2025-1-00885-00643")  # group A, conventional

# A group C employee without a car today: the only thing that a new car in the regular track
# lacks is its Participation, which only Transport_Rep gives. Today without a car pays the
# maintenance of the occupation, so NO_CAR_EMPLOYEE adds one.
EMPLOYEE: dict[str, object] = {
    "eligibility_group": "C",
    "base_salary": "16000",
    "pay_components": [],
    "pension_rate_percent": "6",
    "study_fund_rate_percent": "2.5",
    "credit_points": "2.25",
    "children_under_23": 0,
    "has_current_car": False,
}
NO_CAR_EMPLOYEE: dict[str, object] = {**EMPLOYEE, "occupation": "מנהל/ת מוצר"}
# A salary that no number of a tool result or of a log line can equal by chance.
SALARY = 1234567


def changes(**fields: object) -> ProfileChanges:
    # The values arrive as the model sends them, and are not checked until they are saved.
    return ProfileChanges.model_validate(fields)


def car(key: CarKey) -> CarAlternativeArg:
    return CarAlternativeArg(kind="car", car_key=key)


WAIVER = WaiverAlternativeArg(kind="waiver")


@pytest.fixture(autouse=True)
def tool_call_lines(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)


def logged(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """The tool-call lines written so far, as the stdout formatter renders them."""
    formatter = JsonLinesFormatter()
    lines = [formatter.format(record) for record in caplog.records if record.name == LOGGER]
    return [json.loads(line) for line in lines]


def stored_questions(sessions: sessionmaker[Session]) -> list[RepQuestionRow]:
    with sessions() as session:
        rows = session.scalars(select(RepQuestionRow)).all()
        session.expunge_all()
        return list(rows)


def test_codes_read_the_one_exact_car_with_its_usage_value(ctx: RunContext[AdvisorDeps]) -> None:
    result = lookup_car(
        ctx,
        registration_year=2023,
        vehicle_type_code="1",
        manufacturer_code="01229",
        model_code="00177",
    )

    assert result == FoundCar(
        car=CarRecordDetails(
            car_key=CURRENT_KEY,
            manufacturer="טסלה ארה''ב",
            trade_name="MODEL 3",
            model_code="00177",
            model_description="3E7E",
            vehicle_type_code="1",
            engine_cc=0,
            powertrain=Powertrain.ELECTRIC,
            powertrain_label=copy_he.POWERTRAIN_LABELS["electric"],
            list_price=319000,
            adjusted_price=349006,
            usage_value=7280,
        )
    )


def test_a_key_that_no_record_has_is_not_found(ctx: RunContext[AdvisorDeps]) -> None:
    result = lookup_car(
        ctx,
        registration_year=2023,
        vehicle_type_code="1",
        manufacturer_code="01229",
        model_code="99999",
    )

    assert result == CarNotFound(car_key=CarKey.parse("2026-2023-1-01229-99999"))


@pytest.mark.parametrize(
    ("arguments", "missing"),
    [
        pytest.param(
            {"model_code": "00177"},
            ["registration_year", "vehicle_type_code", "manufacturer_code"],
            id="some-codes",
        ),
        pytest.param({}, ["registration_year", "search_text"], id="nothing"),
        pytest.param({"search_text": "A"}, ["registration_year"], id="search-without-year"),
        pytest.param({"registration_year": 2012}, ["search_text"], id="search-without-text"),
        pytest.param(
            {"registration_year": 2012, "search_text": "  "}, ["search_text"], id="blank-text"
        ),
    ],
)
def test_a_request_that_lacks_inputs_names_them_by_the_parameter_that_carries_them(
    ctx: RunContext[AdvisorDeps], arguments: dict[str, Any], missing: list[str]
) -> None:
    assert lookup_car(ctx, **arguments) == LookupRejected(missing=missing)


def test_a_search_lists_the_candidates_with_what_tells_them_apart(
    ctx: RunContext[AdvisorDeps],
) -> None:
    result = lookup_car(ctx, registration_year=2012, search_text="pathfinder")

    assert isinstance(result, CarCandidates)
    assert (result.match_count, result.selection_required) == (4, True)
    assert (result.none_found, result.truncated) == (False, False)
    assert [
        (item.model_code, item.model_description, item.engine_cc, item.list_price)
        for item in result.candidates
    ] == [
        ("00031", "JVWR51", 2488, 289900),
        ("00038", "JVWR51", 2488, 289900),
        ("00041", "JVWR51", 2488, 329900),
        ("00051", "JAWR51", 2991, 349900),
    ]
    assert {item.powertrain for item in result.candidates} == {Powertrain.CONVENTIONAL}


def test_blank_codes_leave_the_request_a_search(ctx: RunContext[AdvisorDeps]) -> None:
    result = lookup_car(
        ctx,
        registration_year=2012,
        search_text="pathfinder",
        vehicle_type_code=" ",
        manufacturer_code="",
        model_code=None,
    )

    assert isinstance(result, CarCandidates)
    assert result.match_count == 4


def test_a_search_without_a_match_says_that_none_was_found(ctx: RunContext[AdvisorDeps]) -> None:
    result = lookup_car(ctx, registration_year=2012, search_text="no such car")

    assert result == CarCandidates(
        match_count=0,
        candidates=[],
        selection_required=False,
        none_found=True,
        truncated=False,
    )


def test_a_broad_search_gives_the_first_candidates_and_the_number_of_matches(
    ctx: RunContext[AdvisorDeps], lookup: CarLookup
) -> None:
    result = lookup_car(ctx, registration_year=2022, search_text="A")

    everything = lookup.search(2022, "A")
    assert isinstance(everything, Candidates)
    assert isinstance(result, CarCandidates)
    assert result.match_count == len(everything.items) == 4058
    assert [item.car_key for item in result.candidates] == [
        item.key for item in everything.items[:MAX_CANDIDATES]
    ]
    assert (result.truncated, result.selection_required) == (True, True)


def test_the_catalog_lists_the_groups_up_to_the_employees_with_the_powertrain_of_each_car(
    ctx: RunContext[AdvisorDeps], service: ConversationService
) -> None:
    service.update_profile(ctx.deps.conversation_id, {"eligibility_group": "C"})

    result = list_catalog_cars(ctx)

    assert isinstance(result, CatalogToolResult)
    assert result.missing is None
    by_key = {item.car_key: item for item in result.cars}
    assert len(by_key) == 15
    assert {item.group_label for item in result.cars} == {
        copy_he.GROUP_LETTERS[group] for group in (Group.A, Group.B, Group.C)
    }
    assert TESLA_KEY not in by_key
    assert by_key[ATTO_3_KEY].display_name == "BYD Atto 3"
    assert by_key[ATTO_3_KEY].group_label == copy_he.GROUP_LETTERS[Group.C]
    assert by_key[ATTO_3_KEY].powertrain is Powertrain.ELECTRIC
    assert by_key[NIRO_KEY].powertrain is Powertrain.HYBRID
    assert by_key[PICANTO_KEY].powertrain is Powertrain.CONVENTIONAL
    assert [item.display_name for item in result.cars if item.family_track] == [
        "Kia Carnival",
        "Hyundai Santa Fe",
    ]


def test_without_a_stored_group_there_are_no_cars_and_the_field_is_named(
    ctx: RunContext[AdvisorDeps],
) -> None:
    assert list_catalog_cars(ctx) == CatalogToolResult(
        cars=[], missing=FieldLabel.of(MissingField.ELIGIBILITY_GROUP)
    )


@pytest.fixture
def employee(ctx: RunContext[AdvisorDeps], service: ConversationService) -> None:
    service.update_profile(ctx.deps.conversation_id, NO_CAR_EMPLOYEE)


def test_a_waiver_follows_the_occupations_table(
    ctx: RunContext[AdvisorDeps], service: ConversationService, app_config: AppConfig
) -> None:
    table = app_config.occupations.occupations
    allowed = next(item.name for item in table if item.car_waiver_allowed)
    refused = next(item.name for item in table if not item.car_waiver_allowed)
    outcomes = {}
    for occupation in (allowed, refused):
        service.update_profile(ctx.deps.conversation_id, {"occupation": occupation})
        outcomes[occupation] = check_eligibility(ctx, WAIVER)

    assert outcomes[allowed] == EligibilityToolResult(
        status="eligible",
        reason=copy_he.waiver_reason(allowed, allowed=True),
        missing=[],
        is_electric=None,
    )
    assert outcomes[refused] == EligibilityToolResult(
        status="not_eligible",
        reason=copy_he.waiver_reason(refused, allowed=False),
        missing=[],
        is_electric=None,
    )


def test_a_car_of_a_higher_group_is_not_eligible(
    ctx: RunContext[AdvisorDeps], service: ConversationService
) -> None:
    service.update_profile(ctx.deps.conversation_id, {"eligibility_group": "B"})

    assert check_eligibility(ctx, car(TESLA_KEY)) == EligibilityToolResult(
        status="not_eligible",
        reason=copy_he.car_group_reason(Group.D, Group.B, allowed=False),
        missing=[],
        is_electric=True,
    )


def test_missing_inputs_are_listed_by_label_and_no_decision_is_given(
    ctx: RunContext[AdvisorDeps],
) -> None:
    assert check_eligibility(ctx, car(ATTO_3_KEY)) == EligibilityToolResult(
        status="missing",
        reason=copy_he.missing_data([MissingField.ELIGIBILITY_GROUP]),
        missing=[FieldLabel.of(MissingField.ELIGIBILITY_GROUP)],
        is_electric=True,
    )


@pytest.mark.usefixtures("employee")
def test_compare_alternatives_stores_the_list_and_returns_the_stored_comparison(
    ctx: RunContext[AdvisorDeps], service: ConversationService
) -> None:
    conversation_id = ctx.deps.conversation_id

    first = compare_alternatives(ctx, [car(ATTO_3_KEY), WAIVER])

    state = service.state(conversation_id)
    assert state.alternatives == (CarAlternative(car_key=ATTO_3_KEY), WaiverAlternative())
    assert first == state.comparison
    assert [column.position for column in first.alternatives] == [0, 1]

    second = compare_alternatives(ctx, [car(NIRO_KEY)])

    state = service.state(conversation_id)
    assert state.alternatives == (CarAlternative(car_key=NIRO_KEY),)
    assert second == state.comparison
    assert len(second.alternatives) == 1


@pytest.mark.usefixtures("employee")
def test_asking_twice_returns_the_same_open_question(
    ctx: RunContext[AdvisorDeps], sessions: sessionmaker[Session]
) -> None:
    first = ask_rep(ctx, RepField.CURRENT_CAR_PARTICIPATION)
    second = ask_rep(ctx, RepField.CURRENT_CAR_PARTICIPATION)

    assert isinstance(first, RepSent)
    assert isinstance(second, RepSent)
    assert (first.created, second.created) == (True, False)
    assert second.question_id == first.question_id
    [question] = list_for_rep(sessions)
    assert question.id == first.question_id
    label = copy_he.MISSING_FIELD_LABELS[MissingField.CURRENT_CAR_PARTICIPATION]
    assert first.employee_notice == second.employee_notice == copy_he.rep_question_sent(label)


@pytest.mark.usefixtures("employee")
def test_the_model_reads_the_notice_and_no_question_id(ctx: RunContext[AdvisorDeps]) -> None:
    result = ask_rep(ctx, RepField.CURRENT_CAR_PARTICIPATION)

    label = copy_he.MISSING_FIELD_LABELS[MissingField.CURRENT_CAR_PARTICIPATION]
    seen = result.model_dump(mode="json")
    assert seen == {
        "sent": True,
        "created": True,
        "employee_notice": copy_he.rep_question_sent(label),
    }
    # The digits of an ID would count as numbers that a tool returned.
    assert numbers_in(json.dumps(seen, ensure_ascii=False)) == set()


@pytest.mark.usefixtures("employee")
def test_a_participation_question_names_the_car_and_marks_it_as_sent(
    ctx: RunContext[AdvisorDeps], service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    compare_alternatives(ctx, [car(ATTO_3_KEY)])
    field = MissingField.NEW_CAR_PARTICIPATION

    result = ask_rep(ctx, RepField.NEW_CAR_PARTICIPATION, ATTO_3_KEY)

    assert isinstance(result, RepSent)
    assert result.employee_notice == copy_he.rep_question_sent(copy_he.MISSING_FIELD_LABELS[field])
    [question] = list_for_rep(sessions)
    assert question.car is not None
    assert question.car.car_key == ATTO_3_KEY
    comparison = service.state(ctx.deps.conversation_id).comparison
    assert comparison is not None
    column = comparison.alternatives[0]
    assert column.status == "pending"
    assert {item.field: item.sent_to_rep for item in column.missing} == {field: True}


@pytest.mark.usefixtures("employee")
def test_a_storage_failure_returns_the_not_sent_sentence_and_the_comparison_says_not_sent(
    ctx: RunContext[AdvisorDeps],
    service: ConversationService,
    sessions: sessionmaker[Session],
    db_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    compare_alternatives(ctx, [car(ATTO_3_KEY)])
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER refuse_questions BEFORE INSERT ON rep_questions "
                "BEGIN SELECT RAISE(ABORT, 'disk full'); END"
            )
        )
    caplog.clear()
    field = MissingField.NEW_CAR_PARTICIPATION

    result = ask_rep(ctx, RepField.NEW_CAR_PARTICIPATION, ATTO_3_KEY)

    notice = copy_he.rep_question_not_sent(copy_he.MISSING_FIELD_LABELS[field])
    assert result == RepNotSent(employee_notice=notice)
    assert stored_questions(sessions) == []
    comparison = service.state(ctx.deps.conversation_id).comparison
    assert comparison is not None
    assert {item.field: item.sent_to_rep for item in comparison.alternatives[0].missing} == {
        field: False
    }
    [line] = logged(caplog)
    assert (line["tool"], line["ok"]) == ("ask_rep", False)


def racing(times: int) -> Callable[..., tuple[RepQuestionRow, bool]]:
    """Wrap open_or_get so that another request edits the conversation after the first calls."""
    remaining = times

    def open_then_edit(
        conversations: ConversationService,
        session: Session,
        conversation_id: UUID,
        field: RepField,
        chosen: CatalogCar | None,
    ) -> tuple[RepQuestionRow, bool]:
        nonlocal remaining
        outcome = open_or_get(conversations, session, conversation_id, field, chosen)
        if remaining:
            remaining -= 1
            session.connection().execute(
                update(ConversationRow)
                .where(ConversationRow.id == conversation_id)
                .values(version=ConversationRow.version + 1)
            )
        return outcome

    return open_then_edit


@pytest.mark.usefixtures("employee")
def test_a_concurrent_edit_makes_the_call_repeat_once(
    ctx: RunContext[AdvisorDeps],
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "open_or_get", racing(1))

    result = ask_rep(ctx, RepField.CURRENT_CAR_PARTICIPATION)

    assert isinstance(result, RepSent)
    assert result.created is True
    assert len(stored_questions(sessions)) == 1


@pytest.mark.usefixtures("employee")
def test_two_concurrent_edits_in_a_row_leave_the_question_unsent(
    ctx: RunContext[AdvisorDeps],
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "open_or_get", racing(2))

    result = ask_rep(ctx, RepField.CURRENT_CAR_PARTICIPATION)

    assert isinstance(result, RepNotSent)
    assert stored_questions(sessions) == []


@pytest.mark.parametrize(
    ("field", "car_key"),
    [
        pytest.param(RepField.NEW_CAR_PARTICIPATION, None, id="no-car"),
        pytest.param(RepField.NEW_CAR_PARTICIPATION, CURRENT_KEY, id="car-outside-the-catalog"),
        pytest.param(RepField.OCCUPATION, None, id="no-occupation-stored"),
    ],
)
def test_a_call_that_lacks_what_its_question_needs_asks_the_model_to_correct_it(
    ctx: RunContext[AdvisorDeps],
    sessions: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
    field: RepField,
    car_key: CarKey | None,
) -> None:
    with pytest.raises(ModelRetry):
        ask_rep(ctx, field, car_key)

    assert stored_questions(sessions) == []
    [line] = logged(caplog)
    assert (line["ok"], line["error_type"]) == (False, "ModelRetry")


def test_an_occupation_that_is_not_in_the_table_goes_to_the_representative(
    ctx: RunContext[AdvisorDeps], service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    service.update_profile(ctx.deps.conversation_id, {"occupation": "רופא שיניים"})

    result = ask_rep(ctx, RepField.OCCUPATION)

    assert isinstance(result, RepSent)
    [question] = list_for_rep(sessions)
    assert "רופא שיניים" in question.question_text


def test_valid_fields_are_saved_and_echoed_and_invalid_ones_come_back_with_their_range(
    ctx: RunContext[AdvisorDeps], service: ConversationService
) -> None:
    result = update_profile(
        ctx,
        changes(
            eligibility_group="E",
            base_salary=14200,
            pension_rate_percent=101,
            credit_points=2.25,
            children_under_23=-1,
        ),
    )

    ranges = copy_he.PROFILE_FIELD_RANGES
    assert result == ProfileUpdateResult(
        saved={"base_salary": "14200", "credit_points": "2.25"},
        rejected=[
            RejectedField(field="eligibility_group", allowed=ranges["eligibility_group"]),
            RejectedField(field="pension_rate_percent", allowed=ranges["pension_rate_percent"]),
            RejectedField(field="children_under_23", allowed=ranges["children_under_23"]),
        ],
    )
    stored = service.state(ctx.deps.conversation_id).profile
    assert stored == EmployeeProfile(base_salary=Decimal(14200), credit_points=Decimal("2.25"))


def test_a_correction_replaces_the_stored_value(
    ctx: RunContext[AdvisorDeps], service: ConversationService
) -> None:
    update_profile(ctx, changes(base_salary=14200))

    result = update_profile(ctx, changes(base_salary=15300))

    assert result.saved == {"base_salary": "15300"}
    assert service.state(ctx.deps.conversation_id).profile.base_salary == Decimal(15300)


def test_a_null_value_leaves_the_stored_field_alone(
    ctx: RunContext[AdvisorDeps], service: ConversationService
) -> None:
    update_profile(ctx, changes(base_salary=14200))

    result = update_profile(ctx, changes(base_salary=None, occupation="Sales"))

    assert result.saved == {"occupation": "Sales"}
    assert service.state(ctx.deps.conversation_id).profile.base_salary == Decimal(14200)


def test_a_current_car_key_is_saved_only_when_the_dataset_has_it(
    ctx: RunContext[AdvisorDeps], service: ConversationService
) -> None:
    conversation_id = ctx.deps.conversation_id
    description = {"manufacturer": "Zorbo", "model": "Quasar", "registration_year": 2023}

    unknown = update_profile(
        ctx,
        changes(
            current_car_key="2026-2023-1-99999-99999", has_current_car=True, current_car=description
        ),
    )

    assert unknown.saved.keys() == {"has_current_car", "current_car"}
    assert unknown.rejected == [
        RejectedField(
            field="current_car_key", allowed=copy_he.PROFILE_FIELD_RANGES["current_car_key"]
        )
    ]
    profile = service.state(conversation_id).profile
    assert profile.current_car_key is None
    assert profile.has_current_car is True

    known = update_profile(ctx, changes(current_car_key=str(CURRENT_KEY)))

    assert known == ProfileUpdateResult(saved={"current_car_key": str(CURRENT_KEY)}, rejected=[])
    assert service.state(conversation_id).profile.current_car_key == CURRENT_KEY


@pytest.mark.parametrize("key", ["not a key", "2026-2023-1-01229", 7280])
def test_a_malformed_current_car_key_is_not_saved(
    ctx: RunContext[AdvisorDeps], service: ConversationService, key: object
) -> None:
    result = update_profile(ctx, changes(current_car_key=key))

    assert result.saved == {}
    assert [rejected.field for rejected in result.rejected] == ["current_car_key"]
    assert service.state(ctx.deps.conversation_id).profile.current_car_key is None


def test_the_echoed_values_are_numbers_that_the_guard_accepts(
    ctx: RunContext[AdvisorDeps],
) -> None:
    result = update_profile(ctx, changes(base_salary=SALARY, pension_rate_percent="6.5"))

    echoed = ModelRequest(parts=[ToolReturnPart(tool_name="update_profile", content=result)])
    assert {Decimal(SALARY), Decimal("6.5")} <= tool_numbers([echoed])


class ExplodingService(ConversationService):
    """A conversation service that fails like a bug would, with a salary in the message."""

    def state(self, conversation_id: UUID) -> ConversationState:
        raise RuntimeError(f"salary {SALARY}")


def context_of(deps: AdvisorDeps) -> RunContext[AdvisorDeps]:
    return RunContext(deps=deps, model=StubModel(), usage=RunUsage())


def test_a_failing_tool_returns_the_error_shape_and_logs_ok_false_without_amounts(
    deps: AdvisorDeps, sessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    exploding = ExplodingService(sessions, deps.lookup, deps.config)

    result = list_catalog_cars(context_of(replace(deps, conversations=exploding)))

    shape = {
        "error": "list_catalog_cars_failed",
        "action_label": copy_he.TOOL_ACTION_LABELS["list_catalog_cars"],
    }
    assert result == ToolError(**shape)
    assert result.model_dump() == shape
    # An error shape adds no number to what the bot may say.
    assert numbers_in(json.dumps(shape, ensure_ascii=False)) == set()
    [line] = logged(caplog)
    assert (line["event"], line["tool"], line["ok"]) == ("tool_call", "list_catalog_cars", False)
    assert line["error_type"] == "RuntimeError"
    assert line["conversation_id"] == str(deps.conversation_id)
    assert str(SALARY) not in json.dumps(line)


def test_an_unreadable_dataset_makes_the_car_search_return_the_error_shape(
    deps: AdvisorDeps, caplog: pytest.LogCaptureFixture
) -> None:
    # A database without the table of the dataset, whose error quotes the SQL statement.
    unreadable = CarLookup(sessionmaker(create_engine("sqlite+pysqlite://")), 2026)

    result = lookup_car(
        context_of(replace(deps, lookup=unreadable)), registration_year=2012, search_text="a"
    )

    assert result == ToolError(
        error="lookup_car_failed", action_label=copy_he.TOOL_ACTION_LABELS["lookup_car"]
    )
    [line] = logged(caplog)
    assert (line["tool"], line["ok"], line["error_type"]) == (
        "lookup_car",
        False,
        "LookupUnavailable",
    )
    assert "usage_values" not in json.dumps(line)


def test_a_call_writes_one_line_with_the_tool_the_outcome_the_duration_and_the_conversation(
    ctx: RunContext[AdvisorDeps], caplog: pytest.LogCaptureFixture
) -> None:
    lookup_car(ctx, registration_year=2012, search_text="pathfinder")

    [line] = logged(caplog)
    assert set(line) == {"ts", "level", "event", "tool", "ok", "duration_ms", "conversation_id"}
    assert line["event"] == "tool_call"
    assert (line["tool"], line["ok"]) == ("lookup_car", True)
    assert line["conversation_id"] == str(ctx.deps.conversation_id)
    assert line["duration_ms"] >= 0


def test_the_model_names_the_profile_fields_as_the_profile_does() -> None:
    assert list(ProfileChanges.model_fields) == list(EmployeeProfile.model_fields)


def test_every_tool_and_every_powertrain_has_its_hebrew_label() -> None:
    assert set(copy_he.TOOL_ACTION_LABELS) == {tool.__name__ for tool in TOOLS}
    assert set(copy_he.POWERTRAIN_LABELS) == {powertrain.value for powertrain in Powertrain}


def test_the_six_tools_register_as_barriers_that_run_one_at_a_time(deps: AdvisorDeps) -> None:
    model = StubModel(call_tools=[])
    agent = Agent(model, deps_type=AdvisorDeps)
    register_tools(agent)

    asyncio.run(agent.run("hello", deps=deps))

    registered = model.last_model_request_parameters.function_tools
    assert {tool.name for tool in registered} == {
        "lookup_car",
        "list_catalog_cars",
        "check_eligibility",
        "compare_alternatives",
        "ask_rep",
        "update_profile",
    }
    assert all(tool.sequential for tool in registered)


def test_a_real_model_request_fails_during_the_tests() -> None:
    with pytest.raises(RuntimeError):
        models.check_allow_model_requests()
