"""A scripted stand-in for the language model, for the scenario tests of Advisor_Agent.

The script answers the n-th request of a run with its n-th step. A step is a fixed response, or a
function of what the model is shown, so that a script can read a tool result, the retry prompt of
the number guard or the state in the instructions, as the model does.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel


def say(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def call(tool: str, **arguments: object) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, arguments)])


def together(*calls: ModelResponse) -> ModelResponse:
    """Several tool calls in one response, which the agent runs in the order they are given."""
    return ModelResponse(parts=[part for response in calls for part in response.parts])


def _starts_a_run(message: ModelMessage) -> bool:
    return isinstance(message, ModelRequest) and any(
        isinstance(part, UserPromptPart) for part in message.parts
    )


@dataclass(frozen=True)
class Seen:
    """What one request shows the model: the conversation so far, and the instructions."""

    messages: tuple[ModelMessage, ...]
    instructions: str

    @property
    def run(self) -> tuple[ModelMessage, ...]:
        """The messages of the run this request belongs to: its prompt, and what came after."""
        start = max(i for i, message in enumerate(self.messages) if _starts_a_run(message))
        return self.messages[start:]

    @property
    def position(self) -> int:
        """How many responses the model has given in this run."""
        return sum(isinstance(message, ModelResponse) for message in self.run)

    @property
    def prompts(self) -> list[str]:
        """Every prompt of the conversation: the employee's messages, and the notes."""
        return [
            str(part.content)
            for message in self.messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]

    @property
    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        """The tool calls of this run so far, in order, with their arguments."""
        return [
            (part.tool_name, part.args_as_dict())
            for message in self.run
            if isinstance(message, ModelResponse)
            for part in message.parts
            if isinstance(part, ToolCallPart)
        ]

    @property
    def retry_prompt(self) -> str | None:
        """The retry prompt that the last request carries, such as the number guard's."""
        last = self.messages[-1]
        if not isinstance(last, ModelRequest):
            return None
        return next(
            (part.model_response() for part in last.parts if isinstance(part, RetryPromptPart)),
            None,
        )

    def returned(self, tool: str) -> dict[str, Any]:
        """The latest successful result of the tool in the conversation, as the model reads it."""
        for message in reversed(self.messages):
            if not isinstance(message, ModelRequest):
                continue
            for part in reversed(message.parts):
                if (
                    isinstance(part, ToolReturnPart)
                    and part.tool_name == tool
                    and part.outcome == "success"
                ):
                    return part.model_response_object()
        raise LookupError(f"the conversation has no result of {tool}")


type Step = ModelResponse | Callable[[Seen], ModelResponse]


class ScriptedModel:
    """Answers the n-th request of a run with the n-th step, and keeps what each request showed."""

    def __init__(self, *steps: Step) -> None:
        self._steps = steps
        self.requests: list[Seen] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen = Seen(tuple(messages), info.instructions or "")
        self.requests.append(seen)
        if seen.position >= len(self._steps):
            raise AssertionError(
                f"the script has {len(self._steps)} steps, and the run asked for one more"
            )
        step = self._steps[seen.position]
        return step(seen) if callable(step) else step

    @property
    def model(self) -> FunctionModel:
        return FunctionModel(self)
