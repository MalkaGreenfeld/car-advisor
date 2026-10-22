import asyncio
from decimal import Decimal

import pytest
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart

from car_advisor.agent.document_reader import (
    DocumentExtraction,
    DocumentReaderError,
    DocumentReaderTimeout,
    build_reader,
    read_document,
    sniff_media_type,
    MAX_FILE_SIZE,
)
from car_advisor.engine.types import Group


# --- sniff_media_type ---


class TestSniffMediaType:
    def test_pdf_with_matching_magic(self) -> None:
        data = b"%PDF-1.7 rest of file"
        assert sniff_media_type(data, "payslip.pdf") == "application/pdf"

    def test_jpeg_with_matching_magic(self) -> None:
        data = b"\xff\xd8\xff\xe0 rest"
        assert sniff_media_type(data, "scan.jpg") == "image/jpeg"

    def test_jpeg_extension_variant(self) -> None:
        data = b"\xff\xd8\xff\xe0 rest"
        assert sniff_media_type(data, "scan.jpeg") == "image/jpeg"

    def test_png_with_matching_magic(self) -> None:
        data = b"\x89PNG\r\n\x1a\n rest"
        assert sniff_media_type(data, "doc.png") == "image/png"

    def test_wrong_extension(self) -> None:
        data = b"%PDF-1.7 rest"
        assert sniff_media_type(data, "file.txt") is None

    def test_wrong_magic_bytes(self) -> None:
        data = b"not a pdf"
        assert sniff_media_type(data, "fake.pdf") is None

    def test_extension_magic_mismatch(self) -> None:
        data = b"\xff\xd8\xff\xe0 jpeg data"
        assert sniff_media_type(data, "actually.png") is None

    def test_no_extension(self) -> None:
        data = b"%PDF-1.7"
        assert sniff_media_type(data, "noext") is None

    def test_empty_file(self) -> None:
        assert sniff_media_type(b"", "empty.pdf") is None

    def test_case_insensitive_extension(self) -> None:
        data = b"%PDF-1.7 rest"
        assert sniff_media_type(data, "FILE.PDF") == "application/pdf"


# --- DocumentExtraction ---


class TestDocumentExtraction:
    def test_all_none_has_no_field(self) -> None:
        extraction = DocumentExtraction()
        assert not extraction.has_any_field()

    def test_one_field_detected(self) -> None:
        extraction = DocumentExtraction(base_salary=Decimal("10000"))
        assert extraction.has_any_field()

    def test_group_only(self) -> None:
        extraction = DocumentExtraction(eligibility_group=Group.B)
        assert extraction.has_any_field()


# --- read_document ---


def _partial_extraction_model(messages, info):
    return ModelResponse(
        parts=[TextPart(content='{"base_salary": 15000, "pension_rate_percent": 6.5}')]
    )


def _empty_extraction_model(messages, info):
    return ModelResponse(parts=[TextPart(content="{}")])


def _failing_model(messages, info):
    raise RuntimeError("model crashed")


def test_partial_extraction_marks_absent_fields_as_none() -> None:
    model = FunctionModel(_partial_extraction_model)
    reader = build_reader(model)
    result = asyncio.run(read_document(reader, b"%PDF-1.7 fake", "application/pdf"))

    assert result.base_salary == Decimal("15000")
    assert result.pension_rate_percent == Decimal("6.5")
    assert result.eligibility_group is None
    assert result.pay_components is None
    assert result.study_fund_rate_percent is None
    assert result.credit_points is None
    assert result.current_car is None
    assert result.current_car_participation is None
    assert result.has_any_field()


def test_empty_extraction_has_no_fields() -> None:
    model = FunctionModel(_empty_extraction_model)
    reader = build_reader(model)
    result = asyncio.run(read_document(reader, b"%PDF-1.7 fake", "application/pdf"))

    assert not result.has_any_field()


def test_failure_raises_document_reader_error() -> None:
    model = FunctionModel(_failing_model)
    reader = build_reader(model)

    with pytest.raises(DocumentReaderError):
        asyncio.run(read_document(reader, b"%PDF-1.7 fake", "application/pdf"))


def test_timeout_raises_document_reader_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import car_advisor.agent.document_reader as dr_module

    monkeypatch.setattr(dr_module, "_READER_TIMEOUT", 0.01)

    async def _slow_run(*args, **kwargs):
        await asyncio.sleep(10)

    model = FunctionModel(_partial_extraction_model)
    reader = build_reader(model)
    monkeypatch.setattr(reader, "run", _slow_run)

    with pytest.raises(DocumentReaderTimeout):
        asyncio.run(read_document(reader, b"%PDF-1.7 fake", "application/pdf"))
