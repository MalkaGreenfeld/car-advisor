"""Document upload routes: extract fields from a payslip or contract and confirm them.

The file lives only in the request scope. It is never logged, stored in the database or written to
disk (Req 13.9). The checks in criterion 2 run before any model call.
"""

import logging
from asyncio import to_thread
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile
from fastapi.responses import JSONResponse

from car_advisor.agent.document_reader import (
    DocumentExtraction,
    DocumentReaderError,
    DocumentReaderTimeout,
    MAX_FILE_SIZE,
    sniff_media_type,
)
from car_advisor.api import API_PREFIX
from car_advisor.api.schemas import ConversationStateResponse, ErrorBody, ErrorCode
from car_advisor.api.services import Services, get_services

logger = logging.getLogger(__name__)

router = APIRouter(prefix=API_PREFIX, tags=["documents"])

_INVALID_FILE_MESSAGE = (
    "הקובץ אינו תקין. יש להעלות קובץ בפורמט PDF, JPG או PNG בגודל עד 10MB."
)
_NOTHING_DETECTED_MESSAGE = "לא זוהו נתונים במסמך. ניתן להזין את הנתונים ידנית בשיחה או בפרטים שלי."
_READER_FAILED_MESSAGE = "לא ניתן היה לקרוא את המסמך. ניתן להזין את הנתונים ידנית בשיחה או בפרטים שלי."
_READER_TIMEOUT_MESSAGE = "קריאת המסמך ארכה זמן רב מדי. ניתן להזין את הנתונים ידנית בשיחה או בפרטים שלי."


def _error(status: int, code: ErrorCode, message: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code.value}
    if message:
        body["message"] = message
    return JSONResponse(body, status_code=status)


@router.post("/conversations/{conversation_id}/documents")
async def upload_document(
    conversation_id: UUID,
    file: UploadFile,
    services: Annotated[Services, Depends(get_services)],
) -> JSONResponse:
    # Verify the conversation exists.
    await to_thread(services.conversations.state, conversation_id)

    # Read the file into memory.
    data = await file.read()
    filename = file.filename or ""

    # Gate: size (Req 13.2).
    if len(data) == 0 or len(data) > MAX_FILE_SIZE:
        return _error(422, ErrorCode.INVALID_FILE, _INVALID_FILE_MESSAGE)

    # Gate: extension + magic bytes (Req 13.2).
    media_type = sniff_media_type(data, filename)
    if media_type is None:
        return _error(422, ErrorCode.INVALID_FILE, _INVALID_FILE_MESSAGE)

    # The reader is built only when an API key is set.
    if services.reader is None:
        return _error(502, ErrorCode.READER_FAILED, _READER_FAILED_MESSAGE)

    from car_advisor.agent.document_reader import read_document

    try:
        extraction = await read_document(services.reader, data, media_type)
    except DocumentReaderTimeout:
        return _error(504, ErrorCode.READER_TIMEOUT, _READER_TIMEOUT_MESSAGE)
    except DocumentReaderError:
        return _error(502, ErrorCode.READER_FAILED, _READER_FAILED_MESSAGE)
    finally:
        del data

    if not extraction.has_any_field():
        return _error(422, ErrorCode.NOTHING_DETECTED, _NOTHING_DETECTED_MESSAGE)

    return JSONResponse(_extraction_body(extraction), status_code=200)


class _ConfirmRequest:
    pass


@router.post("/conversations/{conversation_id}/documents/confirm")
async def confirm_document(
    conversation_id: UUID,
    body: dict[str, Any],
    background_tasks: BackgroundTasks,
    services: Annotated[Services, Depends(get_services)],
) -> ConversationStateResponse:
    changes = body.get("changes", {})
    if not isinstance(changes, dict):
        return _error(422, ErrorCode.INVALID_FIELDS)  # type: ignore[return-value]

    await to_thread(services.conversations.update_profile, conversation_id, changes)

    background_tasks.add_task(
        services.runner.run_with_note,
        conversation_id,
        "document values confirmed; identify the current car",
    )

    state = await to_thread(services.conversations.state, conversation_id)
    return ConversationStateResponse.from_state(state, agent_available=services.agent_available)


_DOCUMENT_FIELDS = (
    "base_salary",
    "pay_components",
    "pension_rate_percent",
    "study_fund_rate_percent",
    "credit_points",
    "current_car",
    "current_car_participation",
)


def _extraction_body(extraction: DocumentExtraction) -> dict[str, Any]:
    """Build the DocumentCandidates response: { fields, detected }.

    The frontend expects `fields` as a partial ProfileChanges dict and `detected` as the list
    of field names that the reader found.
    """
    fields: dict[str, Any] = {}
    detected: list[str] = []
    for name in _DOCUMENT_FIELDS:
        value = getattr(extraction, name)
        if value is not None:
            fields[name] = _serialize_value(value)
            detected.append(name)
    if extraction.eligibility_group is not None:
        fields["eligibility_group"] = extraction.eligibility_group.value
        detected.append("eligibility_group")
    return {"fields": fields, "detected": detected}


def _serialize_value(value: Any) -> Any:
    from decimal import Decimal

    from car_advisor.engine.types import CurrentCarDescription, PayComponent

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, (CurrentCarDescription, PayComponent)):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return value.value
    return value
