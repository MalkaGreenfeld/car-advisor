"""The rep routes: the questions that Transport_Rep sees, and the answer to one of them.

Rep_Screen has no login, so nothing here returns the conversation ID or anything from the profile
(Req 12.4, 12.5). The conversation is resumed in the background, after the answer is stored.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Response
from pydantic import ValidationError

from car_advisor.agent.runner import answer_note
from car_advisor.api import API_PREFIX
from car_advisor.api.schemas import ErrorBody, ErrorCode, RepAnswerRequest, RepQuestionsResponse
from car_advisor.api.services import Services, get_services
from car_advisor.conversations.profile import FieldError
from car_advisor.conversations.rep_questions import (
    AlreadyAnswered,
    Answered,
    ChoiceAnswer,
    InvalidAnswer,
    ParticipationAnswer,
    QuestionNotFound,
    RepQuestionView,
    SubmittedAnswer,
    TextAnswer,
    answer,
    list_for_rep,
)
from car_advisor.engine import copy_he

router = APIRouter(prefix=API_PREFIX)

ServicesDep = Annotated[Services, Depends(get_services)]


def _question_id(value: str) -> UUID:
    # A malformed ID is a question that does not exist, as it is for a conversation (Req 11.11).
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(404, "no such question") from None


def _field_errors(names: set[str]) -> tuple[FieldError, ...]:
    """The Hebrew message of each named field of the answer form, in the order of the form."""
    return tuple(
        FieldError(name, message)
        for name, message in copy_he.REP_ANSWER_FIELD_ERRORS.items()
        if name in names
    )


def _read(body: dict[str, Any]) -> SubmittedAnswer:
    """Read the body as an answer. The request model checks the shape and each value (Req 12.7)."""
    request = RepAnswerRequest.model_validate(body)
    if (participation := request.participation) is not None:
        return ParticipationAnswer(
            amount=participation.amount,
            effective_date=participation.effective_date,
            note=participation.note,
        )
    if request.choice is not None:
        return ChoiceAnswer(choice=request.choice.strip())
    # The model allows exactly one of the three shapes.
    return TextAnswer(text=request.text or "")


def _invalid(response: Response, fields: tuple[FieldError, ...]) -> ErrorBody:
    response.status_code = 422
    return ErrorBody(code=ErrorCode.INVALID_ANSWER, fields=fields or None)


@router.get("/rep/questions")
def list_questions(services: ServicesDep) -> RepQuestionsResponse:
    return RepQuestionsResponse(questions=tuple(list_for_rep(services.sessions, services.config)))


@router.post(
    "/rep/questions/{question_id}/answer",
    responses={409: {"model": ErrorBody}, 422: {"model": ErrorBody}},
)
def answer_question(
    question_id: str,
    body: Annotated[dict[str, Any], Body()],
    services: ServicesDep,
    background: BackgroundTasks,
    response: Response,
) -> RepQuestionView | ErrorBody:
    question = _question_id(question_id)
    try:
        submitted = _read(body)
    except ValidationError as error:
        # The last part of each location names the field. A body of the wrong shape names none.
        names = {str(item["loc"][-1]) for item in error.errors() if item["loc"]}
        return _invalid(response, _field_errors(names))

    match answer(services.conversations, question, submitted):
        case Answered() as answered:
            # After the response, so a slow or failed resume never fails the answer (Req 12.8).
            background.add_task(
                services.runner.run_with_note, answered.conversation_id, answer_note(answered)
            )
            return answered.question
        case AlreadyAnswered():
            response.status_code = 409
            return ErrorBody(code=ErrorCode.ALREADY_ANSWERED)
        case QuestionNotFound():
            raise HTTPException(404, "no such question")
        case InvalidAnswer(fields):
            return _invalid(response, _field_errors(set(fields)))
