from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from car_advisor.api.schemas import (
    AlternativeRequest,
    CarSearchResponse,
    CatalogCarView,
    CatalogResponse,
    ConversationStateResponse,
    ErrorBody,
    ErrorCode,
    Health,
    MessageRequest,
    ParticipationRequest,
    ProfileChangesRequest,
    RepAnswerRequest,
    RepQuestionsResponse,
)
from car_advisor.conversations.profile import FieldError
from car_advisor.conversations.rep_questions import (
    ParticipationAnswer,
    RepCar,
    RepEmployee,
    RepQuestionStatus,
    RepQuestionType,
    RepQuestionView,
    StoredAnswer,
    TextAnswer,
)
from car_advisor.conversations.service import (
    AgentStatus,
    ComparisonView,
    ConversationState,
    EmployeeGroup,
    MessageRole,
    NoticeCode,
    OpenQuestion,
    StoredMessage,
    TodayView,
)
from car_advisor.dataset.lookup import Candidates, CarCandidate
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

KEY_TEXT = "2026-2026-1-01229-00004"
KEY = CarKey.parse(KEY_TEXT)
PARTICIPATION = {"amount": "350", "effective_date": "2026-01-01"}

COMPARISON = ComparisonView(
    today=TodayView(
        status="pending",
        car=None,
        participation=None,
        usage_value=None,
        ev_refund=None,
        car_maintenance=None,
        net_pay=None,
        missing=(),
    ),
    alternatives=(),
)
STATE = ConversationState(
    id=UUID("6f0c1c2e-3b4a-4c5d-8e9f-0a1b2c3d4e5f"),
    messages=(
        StoredMessage(
            id=12,
            role=MessageRole.ADVISOR,
            text="Hello",
            notice=None,
            created_at=datetime(2026, 10, 13, 9, 12, 3, tzinfo=UTC),
        ),
        StoredMessage(
            id=13,
            role=MessageRole.NOTICE,
            text=None,
            notice=NoticeCode.AGENT_FAILED,
            created_at=datetime(2026, 10, 13, 9, 12, 4, tzinfo=UTC),
        ),
    ),
    profile=EmployeeProfile(
        occupation="מפתח/ת תוכנה", base_salary=Decimal("16000"), pay_components=[]
    ),
    employee_group=EmployeeGroup(group=Group.B, source="occupation"),
    alternatives=(CarAlternative(car_key=KEY), WaiverAlternative()),
    comparison=COMPARISON,
    open_questions=(
        OpenQuestion(
            field=MissingField.NEW_CAR_PARTICIPATION,
            car_display_name="Tesla Model 3",
            car_key=KEY,
        ),
    ),
    agent=AgentStatus(running=False),
)
QUESTION = RepQuestionView(
    id=UUID("0b6c1f7e-5d2a-4e8b-9c3d-1a2b3c4d5e6f"),
    question_type=RepQuestionType.PARTICIPATION,
    question_text="What is the participation?",
    created_at=datetime(2026, 3, 1, 9, 30, tzinfo=UTC),
    employee=RepEmployee(display_name="Employee 4821", number=4821),
    eligibility_group=Group.C,
    car=RepCar(display_name="Tesla Model 3", car_key=KEY),
    status=RepQuestionStatus.ANSWERED,
    answer=StoredAnswer(
        amount=Decimal("350.00"), effective_date=date(2026, 1, 1), note=None, text=None
    ),
)
# What the Rep_Screen may never receive: the conversation ID (Req 12.5), and the salary,
# contribution, document and transcript data (Req 12.4). Matched as parts of a field name.
PRIVATE_NAME_PARTS = (
    "conversation",
    "salary",
    "pay",
    "pension",
    "study_fund",
    "credit",
    "profile",
    "current_car",
    "comparison",
    "document",
    "transcript",
    "message",
)


def error_locations(error: ValidationError) -> list[tuple[int | str, ...]]:
    return [detail["loc"] for detail in error.errors()]


def property_names(schema: object) -> set[str]:
    """Every property name in a JSON schema, including those of nested models."""
    if isinstance(schema, dict):
        own = set(schema.get("properties", ()))
        return own.union(*(property_names(value) for value in schema.values()))
    if isinstance(schema, list):
        return set().union(*(property_names(item) for item in schema))
    return set()


@pytest.mark.parametrize("text", ["x", "x" * 2000, "שלום", "  padded  "])
def test_a_message_of_one_to_two_thousand_characters_is_kept_as_typed(text: str) -> None:
    assert MessageRequest(text=text).text == text


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="spaces"),
        pytest.param(" \t\r\n\u00a0\u3000", id="other-whitespace"),
        pytest.param("x" * 2001, id="2001-characters"),
    ],
)
def test_a_blank_or_over_long_message_is_rejected(text: str) -> None:
    with pytest.raises(ValidationError) as caught:
        MessageRequest(text=text)

    assert error_locations(caught.value) == [("text",)]


def test_a_misspelled_field_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        MessageRequest.model_validate({"text": "hello", "txt": "oops"})

    assert error_locations(caught.value) == [("txt",)]


def test_profile_changes_are_a_free_mapping() -> None:
    changes = {"base_salary": "16000", "children_under_23": None, "no_such_field": [1, {"a": 2}]}

    assert ProfileChangesRequest.model_validate({"changes": changes}).changes == changes


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no-changes"),
        pytest.param({"changes": [("base_salary", "1")]}, id="not-a-mapping"),
        pytest.param({"changes": {}, "chnages": {}}, id="misspelled-sibling"),
    ],
)
def test_profile_changes_need_exactly_a_changes_mapping(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProfileChangesRequest.model_validate(body)


def test_an_alternative_request_reads_the_key_as_text() -> None:
    request = AlternativeRequest.model_validate({"kind": "car", "car_key": KEY_TEXT})

    assert request.car_key == KEY
    assert AlternativeRequest.model_json_schema()["properties"]["car_key"]["type"] == "string"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"kind": "waiver"}, id="waiver"),
        pytest.param({"car_key": KEY_TEXT}, id="no-kind"),
        pytest.param({"kind": "car"}, id="no-key"),
        pytest.param({"kind": "car", "car_key": "2026-2026-1-01229"}, id="short-key"),
        pytest.param({"kind": "car", "car_key": KEY.model_dump()}, id="key-as-mapping"),
        pytest.param({"kind": "car", "car_key": KEY_TEXT, "position": 0}, id="extra-field"),
    ],
)
def test_an_alternative_request_must_be_a_car_with_a_valid_key(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AlternativeRequest.model_validate(body)


@pytest.mark.parametrize("amount", [0, "350.5", "9999999999.99"])
def test_a_rep_answer_can_hold_a_participation(amount: int | str) -> None:
    body = {"participation": {"amount": amount, "effective_date": "2026-01-01", "note": "Note"}}

    answer = RepAnswerRequest.model_validate(body)

    assert answer.participation == ParticipationRequest(
        amount=Decimal(amount), effective_date=date(2026, 1, 1), note="Note"
    )
    assert answer.text is None


def test_a_rep_answer_can_hold_a_text() -> None:
    answer = RepAnswerRequest.model_validate({"text": "Yes, the occupation may give up the car."})

    assert answer.text == "Yes, the occupation may give up the car."
    assert answer.participation is None


@pytest.mark.parametrize(
    ("body", "locations"),
    [
        pytest.param({"participation": PARTICIPATION, "text": "Both"}, [()], id="both"),
        pytest.param({"choice": "B", "text": "Both"}, [()], id="choice-and-text"),
        pytest.param({"choice": ""}, [("choice",)], id="empty-choice"),
        pytest.param({"choice": "x" * 201}, [("choice",)], id="over-long-choice"),
        pytest.param({}, [()], id="neither"),
        pytest.param({"participation": None, "text": None}, [()], id="both-null"),
        pytest.param(
            {"participation": {**PARTICIPATION, "amount": "-1"}},
            [("participation", "amount")],
            id="negative-amount",
        ),
        pytest.param(
            {"participation": {**PARTICIPATION, "amount": "abc"}},
            [("participation", "amount")],
            id="amount-not-a-number",
        ),
        pytest.param(
            {"participation": {**PARTICIPATION, "amount": "350.555"}},
            [("participation", "amount")],
            id="three-decimals",
        ),
        pytest.param(
            {"participation": {**PARTICIPATION, "amount": "10000000000"}},
            [("participation", "amount")],
            id="eleven-whole-digits",
        ),
        pytest.param(
            {"participation": {"effective_date": "2026-01-01"}},
            [("participation", "amount")],
            id="no-amount",
        ),
        pytest.param(
            {"participation": {"amount": "350"}},
            [("participation", "effective_date")],
            id="no-date",
        ),
        pytest.param(
            {"participation": {**PARTICIPATION, "effective_date": "2026-02-31"}},
            [("participation", "effective_date")],
            id="not-a-calendar-date",
        ),
        pytest.param(
            {"participation": {**PARTICIPATION, "note": "n" * 501}},
            [("participation", "note")],
            id="over-long-note",
        ),
        pytest.param(
            {"participation": {**PARTICIPATION, "currency": "ILS"}},
            [("participation", "currency")],
            id="misspelled-participation-field",
        ),
        pytest.param({"text": ""}, [("text",)], id="empty-text"),
        pytest.param({"text": "  \t "}, [("text",)], id="blank-text"),
        pytest.param({"text": "x" * 2001}, [("text",)], id="over-long-text"),
        pytest.param({"text": "Yes", "txt": "oops"}, [("txt",)], id="misspelled-field"),
    ],
)
def test_an_invalid_rep_answer_is_rejected_at_its_field(
    body: dict[str, object], locations: list[tuple[int | str, ...]]
) -> None:
    with pytest.raises(ValidationError) as caught:
        RepAnswerRequest.model_validate(body)

    assert error_locations(caught.value) == locations


def accepted_by(model: type[BaseModel], body: dict[str, object]) -> bool:
    try:
        model.model_validate(body)
    except ValidationError:
        return False
    return True


# The answer route turns the request into these types, so no accepted request may fail there.
@pytest.mark.parametrize(
    "participation",
    [
        pytest.param(PARTICIPATION, id="valid"),
        pytest.param({**PARTICIPATION, "amount": "9999999999.99"}, id="largest-amount"),
        pytest.param({**PARTICIPATION, "amount": "-1"}, id="negative-amount"),
        pytest.param({**PARTICIPATION, "amount": "350.555"}, id="three-decimals"),
        pytest.param({**PARTICIPATION, "amount": "10000000000"}, id="eleven-whole-digits"),
        pytest.param({**PARTICIPATION, "effective_date": "2026-02-31"}, id="not-a-date"),
        pytest.param({**PARTICIPATION, "note": "n" * 500}, id="longest-note"),
        pytest.param({**PARTICIPATION, "note": "n" * 501}, id="over-long-note"),
    ],
)
def test_the_request_takes_the_participations_that_the_answer_type_takes(
    participation: dict[str, object],
) -> None:
    request = accepted_by(RepAnswerRequest, {"participation": participation})

    assert request == accepted_by(ParticipationAnswer, participation)


@pytest.mark.parametrize("text", ["Yes", "x" * 2000, "x" * 2001, "", "   "])
def test_the_request_takes_the_texts_that_the_answer_type_takes(text: str) -> None:
    request = accepted_by(RepAnswerRequest, {"text": text})

    assert request == accepted_by(TextAnswer, {"text": text})


def test_the_state_payload_has_the_fields_of_the_design() -> None:
    body = ConversationStateResponse.from_state(STATE, agent_available=True).model_dump(mode="json")

    assert set(body) == {
        "id",
        "messages",
        "profile",
        "employee_group",
        "alternatives",
        "comparison",
        "open_questions",
        "agent",
    }
    assert body["id"] == "6f0c1c2e-3b4a-4c5d-8e9f-0a1b2c3d4e5f"
    assert body["messages"] == [
        {
            "id": 12,
            "role": "advisor",
            "text": "Hello",
            "notice": None,
            "created_at": "2026-10-13T09:12:03Z",
        },
        {
            "id": 13,
            "role": "notice",
            "text": None,
            "notice": "agent_failed",
            "created_at": "2026-10-13T09:12:04Z",
        },
    ]
    # No Group_Override: the group in use comes from the occupation.
    assert body["profile"]["eligibility_group"] is None
    assert body["employee_group"] == {"group": "B", "source": "occupation"}
    assert body["profile"]["base_salary"] == "16000"
    assert body["profile"]["pay_components"] == []
    assert body["alternatives"] == [{"kind": "car", "car_key": KEY_TEXT}, {"kind": "waiver"}]
    assert body["comparison"] == COMPARISON.model_dump(mode="json")
    assert body["open_questions"] == [
        {
            "field": "new_car_participation",
            "car_display_name": "Tesla Model 3",
            "car_key": KEY_TEXT,
        }
    ]
    assert body["agent"] == {"available": True, "running": False}


@pytest.mark.parametrize("available", [True, False])
@pytest.mark.parametrize("running", [True, False])
def test_agent_status_joins_the_run_guard_and_the_availability(
    available: bool, running: bool
) -> None:
    state = replace(STATE, agent=AgentStatus(running=running))

    payload = ConversationStateResponse.from_state(state, agent_available=available)

    assert payload.agent.model_dump() == {"available": available, "running": running}


@pytest.mark.parametrize("comparison", [COMPARISON, None])
def test_the_state_payload_survives_a_json_round_trip(comparison: ComparisonView | None) -> None:
    state = replace(STATE, comparison=comparison)
    payload = ConversationStateResponse.from_state(state, agent_available=True)

    assert ConversationStateResponse.model_validate_json(payload.model_dump_json()) == payload


def test_a_catalog_car_is_labelled_with_its_group() -> None:
    car = CatalogCar(
        display_name="Tesla Model 3", official_key=KEY, monthly_cost=Decimal("4300"), group=Group.C
    )

    body = CatalogResponse(cars=(CatalogCarView.from_car(car),)).model_dump(mode="json")

    assert body == {
        "cars": [{"car_key": KEY_TEXT, "display_name": "Tesla Model 3", "group": "C"}],
        "missing_field": None,
    }


def test_an_empty_catalog_names_the_missing_field() -> None:
    empty = CatalogResponse(cars=(), missing_field="occupation")

    assert empty.model_dump(mode="json") == {"cars": [], "missing_field": "occupation"}
    with pytest.raises(ValidationError):
        CatalogResponse.model_validate({"cars": [], "missing_field": "base_salary"})


def test_a_search_result_carries_the_details_of_each_candidate() -> None:
    candidate = CarCandidate(
        key=KEY,
        manufacturer_name="TESLA",
        trade_name="MODEL 3",
        model_code="00004",
        model_description="LONG RANGE",
        vehicle_type_code="1",
        engine_cc=0,
        powertrain=Powertrain.ELECTRIC,
        list_price=180000,
    )
    found = Candidates(items=(candidate,), selection_required=False, none_found=False)
    nothing = Candidates(items=(), selection_required=False, none_found=True)

    assert CarSearchResponse.from_candidates(found).model_dump(mode="json") == {
        "candidates": [
            {
                "car_key": KEY_TEXT,
                "manufacturer_name": "TESLA",
                "trade_name": "MODEL 3",
                "model_code": "00004",
                "model_description": "LONG RANGE",
                "vehicle_type_code": "1",
                "engine_cc": 0,
                "powertrain": "electric",
                "list_price": 180000,
            }
        ],
        "selection_required": False,
        "none_found": False,
    }
    assert CarSearchResponse.from_candidates(nothing).model_dump(mode="json") == {
        "candidates": [],
        "selection_required": False,
        "none_found": True,
    }


def test_a_rep_question_has_the_fields_of_the_design() -> None:
    body = RepQuestionsResponse(questions=(QUESTION,)).model_dump(mode="json")

    assert body == {
        "questions": [
            {
                "id": "0b6c1f7e-5d2a-4e8b-9c3d-1a2b3c4d5e6f",
                "question_type": "participation",
                "question_text": "What is the participation?",
                "created_at": "2026-03-01T09:30:00Z",
                "employee": {"display_name": "Employee 4821", "number": 4821},
                "eligibility_group": "C",
                "car": {"display_name": "Tesla Model 3", "car_key": KEY_TEXT},
                "suggested_amount": None,
                "choices": None,
                "status": "answered",
                "answer": {
                    "amount": "350.00",
                    "effective_date": "2026-01-01",
                    "note": None,
                    "text": None,
                    "choice": None,
                },
            }
        ]
    }


@pytest.mark.parametrize("model", [RepQuestionsResponse, RepQuestionView])
def test_the_rep_response_models_cannot_carry_private_data(model: type[BaseModel]) -> None:
    names = property_names(model.model_json_schema(mode="serialization"))

    # Names from nested models show that the walk reaches them.
    assert {"question_text", "display_name", "car_key", "amount"} <= names
    private = [name for name in sorted(names) if any(part in name for part in PRIVATE_NAME_PARTS)]
    assert private == []


def test_health_has_the_fields_of_the_design() -> None:
    health = Health(database_reachable=True, dataset_loaded=True, tax_year=2026, record_count=77988)

    assert health.model_dump(mode="json") == {
        "database_reachable": True,
        "dataset_loaded": True,
        "tax_year": 2026,
        "record_count": 77988,
        "catalog_warnings": [],
    }


def test_an_error_body_leaves_out_its_fields_unless_a_field_is_to_blame() -> None:
    plain = ErrorBody(code=ErrorCode.AGENT_BUSY)
    detailed = ErrorBody(
        code=ErrorCode.INVALID_FIELDS,
        fields=(FieldError("base_salary", "Enter an amount of 0 or more."),),
    )

    assert plain.model_dump(mode="json") == {"code": "agent_busy"}
    assert detailed.model_dump(mode="json") == {
        "code": "invalid_fields",
        "fields": [{"field": "base_salary", "message": "Enter an amount of 0 or more."}],
    }


def test_the_error_codes_are_the_ones_of_the_design() -> None:
    assert {code.value for code in ErrorCode} == {
        "conversation_not_found",
        "agent_busy",
        "conflict",
        "database_unavailable",
        "invalid_fields",
        "missing_inputs",
        "lookup_failed",
        "invalid_answer",
        "already_answered",
        "invalid_file",
        "nothing_detected",
        "reader_failed",
        "reader_timeout",
    }
    with pytest.raises(ValidationError):
        ErrorBody.model_validate({"code": "something_else"})


RESPONSES: dict[str, BaseModel] = {
    "/state": ConversationStateResponse.from_state(STATE, agent_available=True),
    "/catalog": CatalogResponse(cars=(), missing_field="occupation"),
    "/rep": RepQuestionsResponse(questions=(QUESTION,)),
    "/health": Health(
        database_reachable=False, dataset_loaded=False, tax_year=2026, record_count=0
    ),
    "/error": ErrorBody(code=ErrorCode.AGENT_BUSY),
    "/field-error": ErrorBody(
        code=ErrorCode.INVALID_ANSWER, fields=(FieldError("text", "Enter an answer."),)
    ),
}


def serve(body: BaseModel) -> Callable[[], BaseModel]:
    def endpoint() -> BaseModel:
        return body

    return endpoint


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.post("/messages")
    def post_message(body: MessageRequest) -> MessageRequest:
        return body

    for path, body in RESPONSES.items():
        app.add_api_route(path, serve(body), response_model=type(body))
    return TestClient(app)


def test_a_misspelled_field_is_a_422_over_http(client: TestClient) -> None:
    assert client.post("/messages", json={"text": "hello"}).status_code == 200
    assert client.post("/messages", json={"text": "hello", "txt": "oops"}).status_code == 422


@pytest.mark.parametrize("path", list(RESPONSES))
def test_a_response_is_served_as_it_is_dumped(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.json() == RESPONSES[path].model_dump(mode="json")


def test_the_models_have_an_openapi_schema(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 200
