import asyncio
import json
from collections.abc import Callable, Iterable
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.agent.advisor import build_advisor
from car_advisor.agent.number_guard import numbers_in, tool_numbers
from car_advisor.agent.runner import AgentRunner, answer_note
from car_advisor.conversations.rep_questions import (
    Answered,
    ParticipationAnswer,
    RepQuestionStatus,
    RepQuestionType,
    answer,
    list_for_rep,
)
from car_advisor.conversations.service import (
    ComparisonView,
    ConversationService,
    MessageRole,
    OpenQuestion,
)
from car_advisor.dataset.lookup import CarLookup
from car_advisor.engine import copy_he
from car_advisor.engine.money import display
from car_advisor.engine.payroll import PayrollInput, PayrollOk, calculate_net_pay
from car_advisor.engine.types import (
    CarKey,
    EmployeeProfile,
    Group,
    MissingField,
    WaiverAlternative,
)
from car_advisor.rules.loader import AppConfig
from tests.agent.scripted_model import ScriptedModel, Seen, Step, call, say, together
from tests.agent.test_advisor import BASICS, PAY, items_under, labels_to_ask
from tests.agent.test_runner import guard_is_free
from tests.agent.test_tools import ATTO_3_KEY, EMPLOYEE, NO_CAR_EMPLOYEE

LABELS = copy_he.MISSING_FIELD_LABELS
TERMS = copy_he.COMPARISON_TERMS

# Cars of the real dataset: a conventional group C car of the catalog, and a 2021 Kia Seltos that is
# not in the catalog, where two records share the name.
TUCSON_KEY = CarKey.parse("2026-2025-1-00253-00133")
SELTOS_KEY = CarKey.parse("2026-2021-1-00885-00507")
SELTOS_MODEL_CODES = ("00507", "00508")

# The fields that each step of the script asks for, once the steps before it are complete.
PAY_STEP = {
    MissingField.PAY_COMPONENTS,
    MissingField.PENSION_RATE,
    MissingField.STUDY_FUND_RATE,
    MissingField.CREDIT_POINTS,
}
CAR_STEP = {MissingField.CURRENT_CAR, MissingField.CURRENT_CAR_PARTICIPATION}

# The figures of the scripted EV scenario. The representative's participation differs from the
# employee's, so each column shows which source it came from.
BASE_SALARY = 14200
CURRENT_PARTICIPATION = 450
REP_PARTICIPATION = 350
EFFECTIVE_DATE = date(2026, 1, 1)

HELLO = "Hello, I would like to check my company car."
# A net pay that no tool of the scenarios returns.
GUESS = 52000


@pytest.fixture
def conversation_id(service: ConversationService) -> UUID:
    return service.create()


@pytest.fixture
def runner_for(
    service: ConversationService, lookup: CarLookup, app_config: AppConfig
) -> Callable[[ScriptedModel], AgentRunner]:
    def make(script: ScriptedModel) -> AgentRunner:
        return AgentRunner(build_advisor(script.model), service, lookup, app_config)

    return make


def replies(service: ConversationService, conversation_id: UUID) -> list[str]:
    return [
        message.text
        for message in service.state(conversation_id).messages
        if message.role is MessageRole.ADVISOR and message.text is not None
    ]


def stored_history(service: ConversationService, conversation_id: UUID) -> list[ModelMessage]:
    history = service.load_history(conversation_id)
    assert history is not None
    return ModelMessagesTypeAdapter.validate_json(history)


def stored_comparison(service: ConversationService, conversation_id: UUID) -> ComparisonView:
    comparison = service.state(conversation_id).comparison
    assert comparison is not None
    return comparison


def present(value: int | None) -> int:
    assert value is not None
    return value


def named_fields(reply: str) -> set[MissingField]:
    return {field for field, label in LABELS.items() if label in reply}


def bullets(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def shekels(amount: int) -> str:
    return f"₪{amount:,}"


def asking(seen: Seen) -> ModelResponse:
    """Ask for the fields that the instructions list under "Ask now", and for nothing else."""
    return say(bullets(labels_to_ask(seen.instructions)))


@pytest.mark.parametrize(
    ("stored", "asked"),
    [
        pytest.param({}, {MissingField.ELIGIBILITY_GROUP, MissingField.BASE_SALARY}, id="nothing"),
        pytest.param({"base_salary": "14200"}, {MissingField.ELIGIBILITY_GROUP}, id="salary-only"),
        pytest.param(BASICS, PAY_STEP, id="basics"),
        pytest.param(
            {**BASICS, "pay_components": [], "credit_points": "2.25"},
            {MissingField.PENSION_RATE, MissingField.STUDY_FUND_RATE},
            id="some-pay-details",
        ),
        pytest.param(PAY, CAR_STEP, id="pay-details"),
        pytest.param(
            {**PAY, "has_current_car": True, "current_car_participation": "0"},
            {MissingField.CURRENT_CAR},
            id="car-not-identified",
        ),
        pytest.param(
            {**PAY, "has_current_car": False},
            {MissingField.OCCUPATION},
            id="no-car-needs-the-occupation",
        ),
    ],
)
def test_the_reply_names_every_missing_field_of_the_current_step_and_no_stored_field(
    service: ConversationService,
    conversation_id: UUID,
    runner_for: Callable[[ScriptedModel], AgentRunner],
    stored: dict[str, object],
    asked: set[MissingField],
) -> None:
    service.update_profile(conversation_id, stored)

    asyncio.run(runner_for(ScriptedModel(asking)).run_turn(conversation_id, HELLO))

    [reply] = replies(service, conversation_id)
    assert named_fields(reply) == asked


def test_todays_maintenance_is_calculated_once_the_occupation_of_an_employee_with_no_car_is_saved(
    service: ConversationService,
    conversation_id: UUID,
    runner_for: Callable[[ScriptedModel], AgentRunner],
) -> None:
    service.update_profile(conversation_id, {**PAY, "has_current_car": False})

    def today_reply(seen: Seen) -> ModelResponse:
        today = seen.returned("compare_alternatives")["today"]
        return say(f"{TERMS['net_pay']}: {shekels(today['net_pay'])}")

    script = ScriptedModel(
        call("update_profile", changes={"occupation": "מפתח/ת תוכנה"}),
        call("compare_alternatives", alternatives=[]),
        today_reply,
    )

    asyncio.run(runner_for(script).run_turn(conversation_id, "I work as a software developer."))

    today = stored_comparison(service, conversation_id).today
    assert today.status == "calculated"
    assert today.car_maintenance == 1500
    [reply] = replies(service, conversation_id)
    assert numbers_in(reply) == {Decimal(present(today.net_pay))}


def interim_reply(seen: Seen) -> ModelResponse:
    """Tell the employee the notice of ask_rep, then Today, which the comparison already holds."""
    notice = seen.returned("ask_rep")["employee_notice"]
    today = seen.returned("compare_alternatives")["today"]
    return say(f"{notice}\n{TERMS['net_pay']}: {shekels(today['net_pay'])}")


def test_a_participation_the_employee_does_not_know_goes_to_the_representative_not_into_the_reply(
    service: ConversationService,
    sessions: sessionmaker[Session],
    conversation_id: UUID,
    runner_for: Callable[[ScriptedModel], AgentRunner],
) -> None:
    service.update_profile(conversation_id, NO_CAR_EMPLOYEE)
    tucson = {"kind": "car", "car_key": str(TUCSON_KEY)}
    script = ScriptedModel(
        call("check_eligibility", alternative=tucson),
        call("ask_rep", field="new_car_participation", car_key=str(TUCSON_KEY)),
        call("compare_alternatives", alternatives=[tucson]),
        interim_reply,
    )
    message = "I like the Tucson, but I do not know the participation. Maybe 400?"

    asyncio.run(runner_for(script).run_turn(conversation_id, message))

    assert [tool for tool, _ in script.requests[-1].calls] == [
        "check_eligibility",
        "ask_rep",
        "compare_alternatives",
    ]
    [question] = list_for_rep(sessions)
    assert (question.status, question.question_type) == (
        RepQuestionStatus.OPEN,
        RepQuestionType.PARTICIPATION,
    )
    assert question.car is not None and question.car.car_key == TUCSON_KEY
    [open_question] = service.state(conversation_id).open_questions
    assert open_question.field is MissingField.NEW_CAR_PARTICIPATION
    comparison = stored_comparison(service, conversation_id)
    [column] = comparison.alternatives
    assert (column.status, column.participation, column.net_pay) == ("pending", None, None)
    assert [(item.field, item.sent_to_rep) for item in column.missing] == [
        (MissingField.NEW_CAR_PARTICIPATION, True)
    ]
    [reply] = replies(service, conversation_id)
    assert copy_he.rep_question_sent(LABELS[MissingField.NEW_CAR_PARTICIPATION]) in reply
    # The figure that the employee offered is neither used nor repeated: Today's net pay is the
    # only number in the reply.
    assert numbers_in(reply) == {Decimal(present(comparison.today.net_pay))}


def test_a_reply_with_a_number_no_tool_returned_is_sent_back_and_the_final_reply_uses_tool_numbers(
    service: ConversationService,
    conversation_id: UUID,
    runner_for: Callable[[ScriptedModel], AgentRunner],
) -> None:
    service.update_profile(conversation_id, NO_CAR_EMPLOYEE)

    def net_pay_reply(seen: Seen) -> ModelResponse:
        # The first attempt states a figure that no tool returned. The retry prompt makes the
        # second attempt state the one that compare_alternatives returned.
        if seen.retry_prompt is None:
            amount = GUESS
        else:
            amount = seen.returned("compare_alternatives")["today"]["net_pay"]
        return say(f"{TERMS['net_pay']}: {shekels(amount)}")

    script = ScriptedModel(
        call("compare_alternatives", alternatives=[]), net_pay_reply, net_pay_reply
    )

    asyncio.run(runner_for(script).run_turn(conversation_id, HELLO))

    assert [seen.retry_prompt is None for seen in script.requests] == [True, True, False]
    retry = script.requests[-1].retry_prompt
    assert retry is not None and f'"{GUESS:,}"' in retry
    messages = service.state(conversation_id).messages
    assert [message.role for message in messages] == [MessageRole.EMPLOYEE, MessageRole.ADVISOR]
    [reply] = replies(service, conversation_id)
    returned = tool_numbers(stored_history(service, conversation_id))
    today = stored_comparison(service, conversation_id).today
    assert Decimal(GUESS) not in returned
    assert numbers_in(reply) <= returned
    assert numbers_in(reply) == {Decimal(present(today.net_pay))}


def asking_for_what_is_missing(seen: Seen) -> ModelResponse:
    """Ask for the fields that check_eligibility listed as missing, by the labels it returned."""
    missing = seen.returned("check_eligibility")["missing"]
    return say(bullets(item["label"] for item in missing))


def waiver_reply(seen: Seen) -> ModelResponse:
    """State the reason that check_eligibility gave, and the amounts of the waiver column."""
    reason = seen.returned("check_eligibility")["reason"]
    comparison = seen.returned("compare_alternatives")
    column = comparison["alternatives"][0]
    return say(
        "\n".join(
            [
                reason,
                f"{TERMS['car_maintenance']}: {shekels(column['car_maintenance'])}",
                f"{TERMS['net_pay']}: {shekels(comparison['today']['net_pay'])}, "
                f"{shekels(column['net_pay'])}",
                *column["explanation"],
            ]
        )
    )


def test_a_group_b_employee_who_gives_up_the_car_gets_the_decision_the_reason_and_the_amounts(
    service: ConversationService,
    conversation_id: UUID,
    app_config: AppConfig,
    runner_for: Callable[[ScriptedModel], AgentRunner],
) -> None:
    service.update_profile(
        conversation_id,
        {
            **EMPLOYEE,
            "eligibility_group": "B",
            "has_current_car": True,
            "current_car_key": str(SELTOS_KEY),
            "current_car_participation": "400",
        },
    )
    occupation = next(
        item for item in app_config.occupations.occupations if item.car_waiver_allowed
    )
    waiver = {"kind": "waiver"}
    asking_for_the_occupation = ScriptedModel(
        call("check_eligibility", alternative=waiver), asking_for_what_is_missing
    )
    giving_up = ScriptedModel(
        call("update_profile", changes={"occupation": occupation.name}),
        call("check_eligibility", alternative=waiver),
        call("compare_alternatives", alternatives=[waiver]),
        waiver_reply,
    )

    async def scenario() -> None:
        question = "What happens to my net pay if I give up the car?"
        await runner_for(asking_for_the_occupation).run_turn(conversation_id, question)
        await runner_for(giving_up).run_turn(conversation_id, f"I work as {occupation.name}.")

    asyncio.run(scenario())

    # With no occupation, the checker returns no decision, and the reply asks for the occupation.
    asked = asking_for_the_occupation.requests[-1]
    assert asked.calls == [("check_eligibility", {"alternative": waiver})]
    assert asked.returned("check_eligibility")["status"] == "missing"
    first_reply, reply = replies(service, conversation_id)
    assert first_reply == bullets([LABELS[MissingField.OCCUPATION]])

    assert giving_up.requests[-1].calls == [
        ("update_profile", {"changes": {"occupation": occupation.name}}),
        ("check_eligibility", {"alternative": waiver}),
        ("compare_alternatives", {"alternatives": [waiver]}),
    ]
    state = service.state(conversation_id)
    assert state.profile.occupation == occupation.name
    assert state.alternatives == (WaiverAlternative(),)
    comparison = stored_comparison(service, conversation_id)
    [column] = comparison.alternatives
    assert (column.status, column.eligibility.status) == ("calculated", "eligible")
    assert column.eligibility.reason == copy_he.waiver_reason(occupation.name, allowed=True)
    assert (column.usage_value, column.participation) == (0, 0)
    assert column.car_maintenance == occupation.car_maintenance

    assert column.eligibility.reason in reply
    assert numbers_in(reply) <= tool_numbers(stored_history(service, conversation_id))
    stated = {comparison.today.net_pay, column.net_pay, column.car_maintenance}
    assert {Decimal(present(amount)) for amount in stated} <= numbers_in(reply)


def candidates_reply(seen: Seen) -> ModelResponse:
    """List what tells the candidates of lookup_car apart, for the employee to choose from."""
    return say(
        bullets(
            f"{car['model_description']}, {car['model_code']}, {shekels(car['list_price'])}, "
            f"{car['powertrain_label']}"
            for car in seen.returned("lookup_car")["candidates"]
        )
    )


def confirmed_candidate(seen: Seen) -> ModelResponse:
    """Save the key of the candidate that the employee picked from the list of the last turn."""
    picked = next(
        car
        for car in seen.returned("lookup_car")["candidates"]
        if car["model_code"] == SELTOS_KEY.model_code
    )
    return call("update_profile", changes={"current_car_key": picked["car_key"]})


def electric_cars_reply(seen: Seen) -> ModelResponse:
    cars = seen.returned("list_catalog_cars")["cars"]
    return say(
        bullets(
            f"{car['display_name']}, {car['group_label']}, {car['powertrain_label']}"
            for car in cars
            if car["powertrain"] == "electric"
        )
    )


def compare_stored_alternatives(seen: Seen) -> ModelResponse:
    """Pass the alternatives that the state lists on to compare_alternatives, as the note asks."""
    decoder = json.JSONDecoder()
    items = items_under(seen.instructions, "Alternatives in the table")
    alternatives = [decoder.raw_decode(item)[0] for item in items]
    return call("compare_alternatives", alternatives=alternatives)


def answered_field(note: str) -> MissingField:
    return next(field for field in MissingField if f"({field.value})" in note)


def answered_comparison(seen: Seen) -> ModelResponse:
    """Name the field that the note says was answered, then present the comparison it changed."""
    comparison = seen.returned("compare_alternatives")
    column = comparison["alternatives"][0]
    since = date.fromisoformat(column["participation_effective_date"])
    return say(
        "\n".join(
            [
                LABELS[answered_field(seen.prompts[-1])],
                f"{TERMS['participation']}: {shekels(column['participation'])}, {since:%d/%m/%Y}",
                f"{TERMS['net_pay']}: {shekels(comparison['today']['net_pay'])}, "
                f"{shekels(column['net_pay'])}",
                *column["explanation"],
            ]
        )
    )


def net_pay(config: AppConfig, *, usage_value: int, participation: int, ev_refund: Decimal) -> int:
    """The net pay of the scenario's employee for the given car items, from the engine."""
    result = calculate_net_pay(
        PayrollInput(
            base_salary=Decimal(BASE_SALARY),
            commissions=(),
            bonuses=(),
            pension_rate_percent=Decimal(6),
            study_fund_rate_percent=Decimal("2.5"),
            credit_points=Decimal("2.25"),
            usage_value=Decimal(usage_value),
            participation=Decimal(participation),
            ev_refund=ev_refund,
            car_maintenance=Decimal(0),
        ),
        config.rules,
    )
    assert isinstance(result, PayrollOk)
    return display(result.breakdown.net_pay)


def assert_the_ev_comparison_is_calculated(
    comparison: ComparisonView, lookup: CarLookup, config: AppConfig
) -> None:
    """Today and the EV column hold the typed figures, the dataset's Usage_Values and the rules."""
    records = lookup.get_many([SELTOS_KEY, ATTO_3_KEY])
    today_usage_value = records[SELTOS_KEY].usage_value
    column_usage_value = records[ATTO_3_KEY].usage_value
    ev_refund = config.rules.ev_refund.amount
    today = comparison.today
    [column] = comparison.alternatives

    assert (today.status, column.status) == ("calculated", "calculated")
    assert (today.usage_value, today.participation, today.ev_refund) == (
        today_usage_value,
        CURRENT_PARTICIPATION,
        0,
    )
    assert (column.usage_value, column.participation, column.ev_refund) == (
        column_usage_value,
        REP_PARTICIPATION,
        ev_refund,
    )
    assert column.participation_effective_date == EFFECTIVE_DATE
    assert today.net_pay == net_pay(
        config,
        usage_value=today_usage_value,
        participation=CURRENT_PARTICIPATION,
        ev_refund=Decimal(0),
    )
    assert column.net_pay == net_pay(
        config,
        usage_value=column_usage_value,
        participation=REP_PARTICIPATION,
        ev_refund=ev_refund,
    )
    assert column.difference == present(column.net_pay) - present(today.net_pay)


def test_the_scripted_ev_scenario_from_the_first_message_to_the_answer_of_the_representative(
    service: ConversationService,
    sessions: sessionmaker[Session],
    lookup: CarLookup,
    app_config: AppConfig,
    conversation_id: UUID,
    runner_for: Callable[[ScriptedModel], AgentRunner],
) -> None:
    atto_3 = {"kind": "car", "car_key": str(ATTO_3_KEY)}

    async def send(text: str, *steps: Step) -> ScriptedModel:
        script = ScriptedModel(*steps)
        await runner_for(script).run_turn(conversation_id, text)
        return script

    def profile() -> EmployeeProfile:
        return service.state(conversation_id).profile

    def last_reply() -> str:
        return replies(service, conversation_id)[-1]

    async def scenario() -> None:
        await send(
            "I am in group C, and my base salary is 14,200.",
            call("update_profile", changes={"eligibility_group": "C", "base_salary": BASE_SALARY}),
            asking,
        )
        assert (profile().eligibility_group, profile().base_salary) == (Group.C, BASE_SALARY)
        assert named_fields(last_reply()) == PAY_STEP

        await send(
            "No commissions or bonuses, 6% pension, 2.5% study fund and 2.25 credit points.",
            call(
                "update_profile",
                changes={
                    "pay_components": [],
                    "pension_rate_percent": 6,
                    "study_fund_rate_percent": "2.5",
                    "credit_points": "2.25",
                },
            ),
            asking,
        )
        assert profile().pay_components == []
        assert profile().credit_points == Decimal("2.25")
        assert named_fields(last_reply()) == CAR_STEP

        await send(
            "I drive a 2021 Kia Seltos, and I pay 450 a month for it.",
            call(
                "update_profile",
                changes={
                    "has_current_car": True,
                    "current_car": {
                        "manufacturer": "Kia",
                        "model": "Seltos",
                        "registration_year": 2021,
                    },
                    "current_car_participation": CURRENT_PARTICIPATION,
                },
            ),
            call("lookup_car", registration_year=2021, search_text="seltos"),
            candidates_reply,
        )
        # The candidates leave the car unidentified until the employee confirms one (Req 2.9).
        assert profile().has_current_car is True
        assert profile().current_car_participation == CURRENT_PARTICIPATION
        assert profile().current_car_key is None
        assert all(code in last_reply() for code in SELTOS_MODEL_CODES)

        await send(
            "The first one is my car.",
            confirmed_candidate,
            call("list_catalog_cars"),
            electric_cars_reply,
        )
        assert profile().current_car_key == SELTOS_KEY
        assert "BYD Atto 3" in last_reply()
        assert "Tesla Model 3" not in last_reply()

        choice = await send(
            "I want the BYD Atto 3, but I do not know the participation.",
            together(
                call("check_eligibility", alternative=atto_3),
                call("ask_rep", field="new_car_participation", car_key=str(ATTO_3_KEY)),
                call("compare_alternatives", alternatives=[atto_3]),
            ),
            interim_reply,
        )
        assert choice.requests[-1].returned("check_eligibility")["status"] == "eligible"
        [open_question] = service.state(conversation_id).open_questions
        assert open_question == OpenQuestion(
            MissingField.NEW_CAR_PARTICIPATION, "BYD Atto 3", ATTO_3_KEY
        )
        [pending] = stored_comparison(service, conversation_id).alternatives
        assert (pending.status, pending.participation, pending.net_pay) == ("pending", None, None)
        assert [(item.field, item.sent_to_rep) for item in pending.missing] == [
            (MissingField.NEW_CAR_PARTICIPATION, True)
        ]
        assert copy_he.rep_question_sent(LABELS[MissingField.NEW_CAR_PARTICIPATION]) in last_reply()

        [question] = list_for_rep(sessions)
        outcome = answer(
            service,
            question.id,
            ParticipationAnswer(amount=Decimal(REP_PARTICIPATION), effective_date=EFFECTIVE_DATE),
        )
        assert isinstance(outcome, Answered)
        answered = service.state(conversation_id)
        assert answered.open_questions == ()
        assert_the_ev_comparison_is_calculated(
            stored_comparison(service, conversation_id), lookup, app_config
        )

        before = answered.messages
        note = answer_note(outcome)
        resumed = ScriptedModel(compare_stored_alternatives, answered_comparison)
        await runner_for(resumed).run_with_note(conversation_id, note)

        after = service.state(conversation_id)
        assert after.messages[: len(before)] == before
        [added] = after.messages[len(before) :]
        assert (added.role, added.notice) == (MessageRole.ADVISOR, None)
        employee_messages = [m.text for m in before if m.role is MessageRole.EMPLOYEE]
        assert resumed.requests[0].prompts == [*employee_messages, note]
        assert note not in [m.text for m in after.messages]
        assert after.comparison == answered.comparison
        assert guard_is_free(service, conversation_id)

        reply = last_reply()
        assert LABELS[MissingField.NEW_CAR_PARTICIPATION] in reply
        assert f"{EFFECTIVE_DATE:%d/%m/%Y}" in reply
        comparison = stored_comparison(service, conversation_id)
        [column] = comparison.alternatives
        shown = {
            present(comparison.today.net_pay),
            present(column.net_pay),
            present(column.participation),
            abs(present(column.difference)),
        }
        assert {Decimal(number) for number in shown} <= numbers_in(reply)

    asyncio.run(scenario())
