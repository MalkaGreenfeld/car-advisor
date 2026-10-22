"""Document_Reader: extracts Employee_Profile fields from a payslip or contract image/PDF.

The reader is a tool-less pydantic-ai Agent whose output_type is DocumentExtraction. It receives
the file as BinaryContent and returns candidate values for the fields of Req 13.3. A field that
does not appear in the document, or whose value is covered, cut off or unreadable, is left as None
(Req 13.4). The bytes live only in the request scope and are never logged or stored (Req 13.9).
"""

import asyncio
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model

from car_advisor.engine.types import CurrentCarDescription, Group, PayComponent

_READER_TIMEOUT: Final = 60.0

_INSTRUCTIONS: Final = """\
You are a document reader. You receive an image or PDF of a payslip or employment contract.
Extract ONLY the following fields when they are clearly printed in the document:

- eligibility_group: the employee's car eligibility group (A, B, C or D)
- base_salary: the base monthly salary in ₪
- pay_components: additional pay components, each with a kind ("bonus", "seniority", etc.), \
a label and an amount in ₪
- pension_rate_percent: the employee's pension contribution percentage
- study_fund_rate_percent: the employee's study fund contribution percentage
- credit_points: the number of tax credit points
- current_car: the current company car (manufacturer, model, registration year)
- current_car_participation: the monthly participation amount for the current car in ₪

Rules:
- Leave a field null when it is not printed, is covered, cut off, unreadable, or would need \
calculation from other values.
- Never guess, calculate, interpolate or complete a value.
- Never return a net pay amount or a usage value, even if they appear in the document.
- Return only the fields listed above.
"""


class DocumentExtraction(BaseModel, frozen=True):
    """Candidate values read from a document. None means not_detected (Req 13.4)."""

    eligibility_group: Group | None = None
    base_salary: Decimal | None = Field(default=None, ge=0)
    pay_components: list[PayComponent] | None = None
    pension_rate_percent: Decimal | None = Field(default=None, ge=0, le=100)
    study_fund_rate_percent: Decimal | None = Field(default=None, ge=0, le=100)
    credit_points: Decimal | None = Field(default=None, ge=0)
    current_car: CurrentCarDescription | None = None
    current_car_participation: Decimal | None = Field(default=None, ge=0)

    def has_any_field(self) -> bool:
        return any(
            getattr(self, name) is not None for name in type(self).model_fields
        )


# Magic bytes for the three accepted formats.
_PDF_MAGIC: Final = b"%PDF-"
_JPEG_MAGIC: Final = b"\xff\xd8\xff"
_PNG_MAGIC: Final = b"\x89PNG\r\n\x1a\n"

_EXTENSION_MEDIA: Final[dict[str, str]] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

_MAGIC_MEDIA: Final[tuple[tuple[bytes, str], ...]] = (
    (_PDF_MAGIC, "application/pdf"),
    (_JPEG_MAGIC, "image/jpeg"),
    (_PNG_MAGIC, "image/png"),
)

MAX_FILE_SIZE: Final = 10_485_760


def sniff_media_type(data: bytes, filename: str) -> str | None:
    """Return the media type when the extension and the magic bytes agree, or None."""
    dot = filename.rfind(".")
    if dot < 0:
        return None
    ext = filename[dot:].lower()
    expected = _EXTENSION_MEDIA.get(ext)
    if expected is None:
        return None
    for magic, media in _MAGIC_MEDIA:
        if data[:len(magic)] == magic:
            if media == expected:
                return media
            return None
    return None


def build_reader(model: Model) -> Agent[None, DocumentExtraction]:
    return Agent(
        model,
        output_type=DocumentExtraction,
        instructions=_INSTRUCTIONS,
    )


class DocumentReaderError(Exception):
    pass


class DocumentReaderTimeout(Exception):
    pass


async def read_document(
    reader: Agent[None, DocumentExtraction],
    data: bytes,
    media_type: str,
) -> DocumentExtraction:
    """Run the reader agent on the file bytes and return the extraction.

    Raises DocumentReaderError on failure and DocumentReaderTimeout after 60 s.
    """
    try:
        result = await asyncio.wait_for(
            reader.run(
                [BinaryContent(data=data, media_type=media_type)],
            ),
            timeout=_READER_TIMEOUT,
        )
        return result.output
    except TimeoutError as exc:
        raise DocumentReaderTimeout("document reader timed out") from exc
    except Exception as exc:
        if isinstance(exc, DocumentReaderTimeout):
            raise
        raise DocumentReaderError(f"document reader failed: {type(exc).__name__}") from exc
