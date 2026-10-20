import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from decimal import Decimal
from typing import Final

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ToolReturnPart

# A dotted date comes first, so 15.3.2026 is a day, a month and a year and not 15.3 and 2026. A
# thousands comma needs exactly three digits after it, so 2019,2020 and 12,3456 are not one number.
_NUMBER: Final = re.compile(
    r"(?P<date>\d{1,2}\.\d{1,2}\.\d{2,4})(?!\d)"
    r"|\d{1,3}(?:,\d{3})+(?!\d)(?:\.\d+)?"
    r"|\d+(?:\.\d+)?"
)


def numbers_in(text: str) -> set[Decimal]:
    """Return the values of the numbers that the text shows.

    A number is a run of digits, of any script, so 12,345 is 12345 and 6.5 is 6.5. Signs, the
    currency sign and the percent sign are ignored, and so are leading zeros: 01229 is 1229. Digits
    count wherever they stand, inside a word or an Official_Key too, and a date yields its day,
    month and year whether it is written 15/03/2026, 15.3.2026 or 2026-03-15.
    """
    return {value for value, _ in _read(text)}


def tool_numbers(messages: Sequence[ModelMessage]) -> set[Decimal]:
    """Return every number in the successful tool results of the messages, nested ones included.

    Nothing else counts: not the employee's words, not the model's own text or tool arguments, not
    a retry prompt, and not the keys, booleans or metadata of a result.
    """
    return {
        number
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.outcome == "success"
        for number in _values_in(part.model_response_object())
    }


def check_reply(ctx: RunContext[object], reply: str) -> str:
    """Return the reply, or ask the model to retry if it holds a number no tool returned (Req 9.5).

    The retry message names the unknown numbers as the reply wrote them. It repeats nothing else
    from the conversation, so a salary that a tool returned never reaches it.
    """
    known = tool_numbers(ctx.messages)
    unknown: dict[Decimal, str] = {}
    for value, written in _read(reply):
        if value not in known:
            unknown.setdefault(value, written)
    if unknown:
        raise ModelRetry(_retry_message(unknown.values()))
    return reply


def _read(text: str) -> Iterator[tuple[Decimal, str]]:
    for match in _NUMBER.finditer(_plain(text)):
        written = match.group()
        for part in written.split(".") if match.lastgroup == "date" else (written,):
            yield Decimal(part.replace(",", "")), part


def _plain(text: str) -> str:
    # NFKC turns full-width digits and commas, superscripts and circled digits into plain ones.
    # Format characters (zero-width, bidirectional marks) are invisible, so they must not split a
    # number that the screen shows as one.
    folded = unicodedata.normalize("NFKC", text)
    return "".join(char for char in folded if unicodedata.category(char) != "Cf")


def _values_in(value: object) -> Iterator[Decimal]:
    match value:
        case bool():
            # A flag is not the number 1, although bool is an int.
            return
        case int() | float():
            number = Decimal(repr(value))
            if number.is_finite():
                # copy_abs keeps every digit, where abs() rounds to the context precision.
                yield number.copy_abs()
        case str():
            yield from numbers_in(value)
        case dict():
            for item in value.values():
                yield from _values_in(item)
        case list():
            for item in value:
                yield from _values_in(item)


def _retry_message(unknown: Iterable[str]) -> str:
    listed = ", ".join(f'"{written}"' for written in unknown)
    return (
        f"The reply contains numbers that no tool returned in this conversation: {listed}. "
        "Every number in a reply must be in a tool result with the same value, so never calculate, "
        "estimate or round. A number that the employee wrote counts only after a tool returns it. "
        "Call the tool that returns the number, or leave the number out."
    )
