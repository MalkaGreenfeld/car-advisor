import asyncio
from datetime import date
from decimal import Decimal

import pytest
from pydantic_ai import Agent, AgentRunResult, ModelRetry, RunContext, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

# Aliased, because pytest would try to collect a class named Test* as a test class.
from pydantic_ai.models.test import TestModel as StubModel
from pydantic_ai.usage import RunUsage

from car_advisor.agent.number_guard import check_reply, numbers_in, tool_numbers
from car_advisor.engine import copy_he
from car_advisor.engine.explanation import BreakdownItem, build_explanation
from car_advisor.engine.types import CarKey, EmployeeProfile

D = Decimal

CAR_KEY = CarKey.parse("2026-2020-1-01229-00001")
NET_PAY = 12345


def tool_result(content: object) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name="compare_alternatives", content=content)])


def employee_says(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(text)])


def context(*messages: ModelMessage) -> RunContext[None]:
    return RunContext(deps=None, model=StubModel(), usage=RunUsage(), messages=list(messages))


def net_pay_sentence(amount: str) -> str:
    return copy_he.EXPLANATION_HEADLINE_UP.format(a=amount)


def explanation() -> tuple[str, ...]:
    return build_explanation(
        210,
        [
            (BreakdownItem.USAGE_VALUE, 310),
            (BreakdownItem.EV_REFUND, 250),
            (BreakdownItem.PARTICIPATION, -350),
        ],
    )


def test_a_shekel_amount_with_a_thousands_comma_matches_the_number_of_a_tool_result() -> None:
    ctx = context(tool_result({"net_pay": NET_PAY}))

    assert numbers_in("₪12,345") == {D(NET_PAY)}
    assert check_reply(ctx, "₪12,345") == "₪12,345"


def test_an_unknown_number_raises_model_retry_naming_it() -> None:
    ctx = context(tool_result({"net_pay": NET_PAY}))

    with pytest.raises(ModelRetry) as raised:
        check_reply(ctx, "₪12,346")

    assert "12,346" in raised.value.message


def test_a_number_from_the_employee_counts_only_after_a_tool_echoes_it() -> None:
    typed = employee_says("my base salary is 18000")
    saving = ModelResponse(
        parts=[ToolCallPart("update_profile", {"changes": {"base_salary": 18000}})]
    )
    echo = tool_result(EmployeeProfile(base_salary=D(18000)))

    with pytest.raises(ModelRetry):
        check_reply(context(typed), "₪18,000")
    # The arguments of the model's own call are not a result either.
    with pytest.raises(ModelRetry):
        check_reply(context(typed, saving), "₪18,000")
    assert check_reply(context(typed, saving, echo), "₪18,000") == "₪18,000"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("₪12,345", {12345}, id="amount-with-sign-and-thousands-comma"),
        pytest.param("12345", {12345}, id="integer"),
        pytest.param("₪1,234,567.50", {D("1234567.5")}, id="amount-with-two-commas-and-decimals"),
        pytest.param("6.5%", {D("6.5")}, id="percentage"),
        pytest.param("-₪120 and (120)", {120}, id="signs-are-ignored"),
        pytest.param("2019,2020", {2019, 2020}, id="comma-before-four-digits-is-no-thousands"),
        pytest.param("12,3456", {12, 3456}, id="comma-before-more-than-three-digits"),
        pytest.param("1,23", {1, 23}, id="comma-before-fewer-than-three-digits"),
        pytest.param("2026-2020-1-01229-00001", {2026, 2020, 1, 1229}, id="official-key"),
        pytest.param("15/03/2026", {15, 3, 2026}, id="date-with-slashes"),
        pytest.param("15.3.2026", {15, 3, 2026}, id="date-with-dots"),
        pytest.param("2026-03-15", {15, 3, 2026}, id="iso-date"),
        pytest.param("RAV4, ב2020", {4, 2020}, id="digits-inside-words"),
        pytest.param("٥٢", {52}, id="arabic-indic-digits"),
        pytest.param("５２，０００", {52000}, id="full-width-digits-and-comma"),
        pytest.param("₪52\u200b,000 ₪52\u200e000", {52000}, id="invisible-characters"),
        pytest.param("", set(), id="empty"),
        pytest.param(copy_he.EXPLANATION_HEADLINE_UNCHANGED, set(), id="text-without-digits"),
    ],
)
def test_numbers_in_reads_the_values_that_a_reader_sees(text: str, expected: set[int]) -> None:
    assert numbers_in(text) == {D(value) for value in expected}


def test_numbers_in_reads_the_amounts_of_the_explanation_copy() -> None:
    assert numbers_in(" ".join(explanation())) == {D(210), D(310), D(250), D(350)}


def test_tool_numbers_reads_every_nested_value_of_a_result() -> None:
    result = {
        "net_pay": NET_PAY,
        "difference": -120,
        "rate": 6.5,
        "credit_points": D("2.25"),
        "alternatives": [{"effective_date": date(2026, 1, 1), "car_key": CAR_KEY}],
        "explanation": ["₪210", "₪1,000"],
    }

    assert tool_numbers([tool_result(result)]) == {
        D(NET_PAY),
        D(120),
        D("6.5"),
        D("2.25"),
        D(2026),
        D(1),
        D(2020),
        D(1229),
        D(210),
        D(1000),
    }


def test_tool_numbers_ignores_everything_but_the_content_of_successful_results() -> None:
    messages = [
        ModelRequest(
            parts=[SystemPromptPart("limit 111"), UserPromptPart("salary 222")],
            instructions="stored salary 333",
        ),
        ModelResponse(
            parts=[TextPart("net pay 444"), ToolCallPart("update_profile", {"base_salary": 555})]
        ),
        ModelRequest(parts=[RetryPromptPart("unknown number 666")]),
        tool_result({"kept": 777, "children_under_23": None, "is_electric": True}),
        ModelRequest(parts=[ToolReturnPart("a_tool", "error 888", outcome="failed")]),
        ModelRequest(parts=[ToolReturnPart("a_tool", "ok", metadata={"hidden": 999})]),
    ]

    assert tool_numbers(messages) == {D(777)}


def test_tool_numbers_of_the_stored_history_equal_those_of_the_live_one() -> None:
    live = [
        tool_result(EmployeeProfile(base_salary=D(18000), credit_points=D("2.25"))),
        tool_result({"car_key": CAR_KEY, "effective_date": date(2026, 1, 1)}),
    ]
    stored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(live))

    assert tool_numbers(stored) == tool_numbers(live)
    assert D(18000) in tool_numbers(stored)


def test_a_reply_without_numbers_needs_no_tool_result() -> None:
    reply = copy_he.EXPLANATION_HEADLINE_UNCHANGED

    assert check_reply(context(), reply) == reply


def test_no_number_is_allowed_without_a_tool_result_not_even_a_list_marker() -> None:
    with pytest.raises(ModelRetry):
        check_reply(context(), f"1. {copy_he.EXPLANATION_HEADLINE_UNCHANGED}")


def test_a_reply_may_repeat_the_explanation_that_a_tool_returned() -> None:
    lines = explanation()
    ctx = context(tool_result({"explanation": lines}))
    reply = " ".join(lines)

    assert check_reply(ctx, reply) == reply
    with pytest.raises(ModelRetry):
        check_reply(ctx, reply.replace("310", "311"))


def test_the_sign_of_a_tool_number_does_not_matter() -> None:
    ctx = context(tool_result({"difference": -120}))

    assert check_reply(ctx, "₪120") == "₪120"


def test_a_tool_number_with_more_digits_than_the_decimal_precision_is_kept_exactly() -> None:
    long = 10**30 + 1
    ctx = context(tool_result({"count": -long}))

    assert check_reply(ctx, str(long)) == str(long)
    with pytest.raises(ModelRetry):
        check_reply(ctx, str(long + 1))


def test_a_flag_in_a_result_is_not_the_number_one() -> None:
    ctx = context(tool_result({"is_electric": True}))

    with pytest.raises(ModelRetry):
        check_reply(ctx, "1")


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param("₪52\u200b000", id="zero-width-space"),
        pytest.param("₪52\u200f000", id="right-to-left-mark"),
        pytest.param("₪５２０００", id="full-width-digits"),
    ],
)
def test_a_format_character_cannot_hide_an_unknown_number(reply: str) -> None:
    # 52 and 0 are known, so reading the reply as two numbers would let it through.
    ctx = context(tool_result({"first": 52, "second": 0}))

    with pytest.raises(ModelRetry):
        check_reply(ctx, reply)


def test_the_retry_names_the_unknown_numbers_of_the_reply_and_nothing_else() -> None:
    ctx = context(tool_result({"base_salary": "18000", "net_pay": NET_PAY}))
    reply = f"{net_pay_sentence('12,345')}, ₪52,000 and again ₪52000, and 7.5%"

    with pytest.raises(ModelRetry) as raised:
        check_reply(ctx, reply)

    message = raised.value.message
    assert numbers_in(message) == {D(52000), D("7.5")}
    assert "52,000" in message
    assert reply not in message


def agent_with(*replies: ModelResponse) -> tuple[Agent[None, str], list[list[ModelMessage]]]:
    """Build an agent whose model plays the replies in turn and records what it was sent."""
    script = iter(replies)
    sent: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        sent.append(list(messages))
        return next(script)

    agent = Agent(FunctionModel(respond), output_type=str, retries={"output": 2})

    @agent.tool_plain
    def net_pay() -> int:
        return NET_PAY

    agent.output_validator(check_reply)
    return agent, sent


def say(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def call_net_pay() -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("net_pay", {})])


def run(
    agent: Agent[None, str], prompt: str, history: list[ModelMessage] | None = None
) -> AgentRunResult[str]:
    return asyncio.run(agent.run(prompt, message_history=history))


def test_a_run_retries_a_reply_with_a_number_that_no_tool_returned() -> None:
    right = net_pay_sentence("12,345")
    agent, sent = agent_with(call_net_pay(), say(f"{right}, ₪52,000"), say(right))

    result = run(agent, "how much is my net pay?")

    assert result.output == right
    retry = sent[2][-1].parts[0]
    assert isinstance(retry, RetryPromptPart)
    assert isinstance(retry.content, str)
    assert numbers_in(retry.content) == {D(52000)}


@pytest.mark.parametrize("stored", [False, True], ids=["live-history", "stored-history"])
def test_a_later_turn_may_use_the_numbers_that_a_tool_returned_in_an_earlier_one(
    stored: bool,
) -> None:
    first_agent, _ = agent_with(call_net_pay(), say(copy_he.EXPLANATION_HEADLINE_UNCHANGED))
    history = run(first_agent, "how much is my net pay?").all_messages()
    if stored:
        history = ModelMessagesTypeAdapter.validate_json(
            ModelMessagesTypeAdapter.dump_json(history)
        )
    reply = net_pay_sentence("12,345")
    second_agent, _ = agent_with(say(reply))

    result = run(second_agent, "say it again", history)

    assert result.output == reply


def test_a_number_named_in_a_retry_prompt_does_not_become_known() -> None:
    wrong = say("₪52,000")
    agent, _ = agent_with(wrong, wrong, wrong)

    with pytest.raises(UnexpectedModelBehavior):
        run(agent, "how much is my net pay?")
