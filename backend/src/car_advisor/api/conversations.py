"""The employee routes: the conversation as the Employee_Screen uses it, and the car search.

A route that only calls the conversation service is a plain def, which FastAPI runs in its thread
pool, so a slow database cannot block the event loop or the health route. The message route awaits
the agent, and reads the state in a thread.
"""

import logging
import re
from asyncio import to_thread
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from car_advisor.api import API_PREFIX
from car_advisor.api.schemas import (
    AlternativeRequest,
    CarSearchResponse,
    CatalogCarView,
    CatalogResponse,
    ConversationStateResponse,
    ErrorBody,
    ErrorCode,
    MessageRequest,
    OccupationsResponse,
    ProfileChangesRequest,
)
from car_advisor.api.services import Services, get_services
from car_advisor.conversations.profile import FieldError
from car_advisor.conversations.service import (
    AlternativeNotFound,
    ConversationState,
    parse_conversation_id,
)
from car_advisor.dataset.lookup import Candidates, LookupFailed, Rejected
from car_advisor.engine import copy_he
from car_advisor.engine.types import CarAlternative

# The registration years of the dataset have four digits, and so has the year of a search.
_YEAR: Final = re.compile(r"[0-9]{4}")

router = APIRouter(prefix=API_PREFIX)
logger = logging.getLogger(__name__)

ServicesDep = Annotated[Services, Depends(get_services)]


async def _conversation_id(conversation_id: str) -> UUID:
    """Read the ID of the path. A malformed one is a conversation that does not exist (Req 11.11).

    The parameter is text because a UUID type would answer a malformed ID with a 422. The function
    is async like get_services, so that it never waits for a thread of the pool.
    """
    return parse_conversation_id(conversation_id)


ConversationId = Annotated[UUID, Depends(_conversation_id)]


def _respond(services: Services, state: ConversationState) -> ConversationStateResponse:
    return ConversationStateResponse.from_state(state, agent_available=services.agent_available)


@router.post("/conversations", status_code=201)
def create_conversation(services: ServicesDep) -> ConversationStateResponse:
    conversation_id = services.conversations.create()
    return _respond(services, services.conversations.state(conversation_id))


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: ConversationId, services: ServicesDep
) -> ConversationStateResponse:
    # Reading never creates a conversation (Req 11.11).
    return _respond(services, services.conversations.state(conversation_id))


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: ConversationId, body: MessageRequest, services: ServicesDep
) -> ConversationStateResponse:
    # A run that fails is not an error here: it ends with a notice in the state (Req 9.15).
    await services.runner.run_turn(conversation_id, body.text)
    state = await to_thread(services.conversations.state, conversation_id)
    return _respond(services, state)


@router.patch("/conversations/{conversation_id}/profile")
def patch_profile(
    conversation_id: ConversationId, body: ProfileChangesRequest, services: ServicesDep
) -> ConversationStateResponse:
    # All or nothing: one invalid field stores none of them (Req 11.3).
    services.conversations.update_profile(conversation_id, body.changes)
    return _respond(services, services.conversations.state(conversation_id))


@router.put("/conversations/{conversation_id}/alternatives/{position}")
def replace_alternative(
    conversation_id: ConversationId,
    position: int,
    body: AlternativeRequest,
    services: ServicesDep,
) -> ConversationStateResponse:
    # The comparison is recalculated by the service, and the model is not involved (Req 11.8).
    alternative = CarAlternative(car_key=body.car_key)
    try:
        state = services.conversations.replace_alternative(conversation_id, position, alternative)
    except AlternativeNotFound:
        # No code of the error table fits a column that does not exist, so this is a plain 404.
        raise HTTPException(404, "no alternative at this position") from None
    return _respond(services, state)


@router.get("/conversations/{conversation_id}/catalog")
def get_catalog(conversation_id: ConversationId, services: ServicesDep) -> CatalogResponse:
    group = services.conversations.state(conversation_id).employee_group.group
    if group is None:
        # No cars from any group in its place; the group follows from the occupation (Req 4.17).
        return CatalogResponse(cars=(), missing_field="occupation")
    config = services.config
    cars = config.catalog.cars_for(group, config.rules.groups.order)
    return CatalogResponse(cars=tuple(CatalogCarView.from_car(car) for car in cars))


@router.get("/occupations")
def get_occupations(services: ServicesDep) -> OccupationsResponse:
    # The table's names in its order, for the suggestions of the occupation field (Req 11.19).
    names = (item.name.strip() for item in services.config.occupations.occupations)
    return OccupationsResponse(names=tuple(names))


def _registration_year(value: str | None) -> int | None:
    """Read the year of a search. One that is absent, blank or not four digits counts as missing."""
    year = "" if value is None else value.strip()
    return int(year) if _YEAR.fullmatch(year) else None


def _lookup_failed(response: Response, error_type: str) -> ErrorBody:
    # The type only: the message of a database error can quote the SQL and its parameters.
    logger.error("lookup_failed", extra={"stage": "car_search", "error_type": error_type})
    response.status_code = 503
    return ErrorBody(code=ErrorCode.LOOKUP_FAILED)


@router.get("/cars/search", responses={422: {"model": ErrorBody}, 503: {"model": ErrorBody}})
def search_cars(
    services: ServicesDep,
    response: Response,
    registration_year: str | None = None,
    text: str | None = None,
) -> CarSearchResponse | ErrorBody:
    try:
        found = services.lookup.search(_registration_year(registration_year), text)
    except Exception as error:
        # Car_Lookup reports a database error as LookupFailed. This is any error it did not expect.
        return _lookup_failed(response, type(error).__name__)
    match found:
        case Candidates():
            return CarSearchResponse.from_candidates(found)
        case Rejected(missing):
            response.status_code = 422
            absent = (FieldError(name, copy_he.SEARCH_INPUT_REQUIRED[name]) for name in missing)
            return ErrorBody(code=ErrorCode.MISSING_INPUTS, fields=tuple(absent))
        case LookupFailed():
            return _lookup_failed(response, type(found).__name__)
