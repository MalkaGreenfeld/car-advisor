import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart

from car_advisor.agent.document_reader import (
    DocumentExtraction,
    DocumentReaderError,
    DocumentReaderTimeout,
    build_reader,
)
from car_advisor.api.app import create_app
from car_advisor.api.services import Services
from car_advisor.settings import Settings
from tests.api.conftest import PASSWORD, make_settings, write_catalog, write_dataset


# Minimal valid PDF-like bytes that pass the magic-byte check.
PDF_BYTES = b"%PDF-1.7 fake content padding to make it non-empty"
JPEG_BYTES = b"\xff\xd8\xff\xe0 fake jpeg"
PNG_BYTES = b"\x89PNG\r\n\x1a\n fake png"


def _extraction_model(messages, info):
    return ModelResponse(
        parts=[TextPart(content='{"base_salary": 15000, "pension_rate_percent": 6.5}')]
    )


def _empty_model(messages, info):
    return ModelResponse(parts=[TextPart(content="{}")])


@pytest.fixture
def app_settings(tmp_path: Path) -> Settings:
    from tests.dataset.test_import import record

    write_dataset(
        tmp_path / "usage-values.csv", record(2026, "00001", 7990), record(2026, "00002", 8100)
    )
    write_catalog(tmp_path)
    return make_settings(tmp_path)


@pytest.fixture
def client(
    app_settings: Settings, sqlite_startup: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    import car_advisor.api.app as app_module

    reader = build_reader(FunctionModel(_extraction_model))

    original_start = app_module.start_services

    def patched_start(settings: Settings, config: Any, engine: Any) -> Services:
        services = original_start(settings, config, engine)
        return Services(
            settings=services.settings,
            config=services.config,
            sessions=services.sessions,
            lookup=services.lookup,
            conversations=services.conversations,
            runner=services.runner,
            catalog_warnings=services.catalog_warnings,
            reader=reader,
        )

    monkeypatch.setattr(app_module, "start_services", patched_start)
    app = create_app(app_settings)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def conversation_id(client: TestClient) -> UUID:
    response = client.post("/api/conversations")
    return UUID(response.json()["id"])


def _upload(client: TestClient, conversation_id: UUID, data: bytes, filename: str) -> Any:
    return client.post(
        f"/api/conversations/{conversation_id}/documents",
        files={"file": (filename, data)},
    )


class TestUpload:
    def test_valid_pdf_returns_candidates(self, client: TestClient, conversation_id: UUID) -> None:
        response = _upload(client, conversation_id, PDF_BYTES, "payslip.pdf")

        assert response.status_code == 200
        body = response.json()
        assert "fields" in body
        assert "detected" in body
        assert body["fields"]["base_salary"] == "15000"
        assert body["fields"]["pension_rate_percent"] == "6.5"
        assert "base_salary" in body["detected"]
        assert "pension_rate_percent" in body["detected"]
        assert "eligibility_group" not in body["detected"]

    def test_wrong_extension_rejected(self, client: TestClient, conversation_id: UUID) -> None:
        response = _upload(client, conversation_id, PDF_BYTES, "payslip.txt")

        assert response.status_code == 422
        assert response.json()["code"] == "invalid_file"

    def test_empty_file_rejected(self, client: TestClient, conversation_id: UUID) -> None:
        response = _upload(client, conversation_id, b"", "payslip.pdf")

        assert response.status_code == 422
        assert response.json()["code"] == "invalid_file"

    def test_oversized_file_rejected(self, client: TestClient, conversation_id: UUID) -> None:
        data = b"%PDF-" + b"\x00" * (10_485_761)
        response = _upload(client, conversation_id, data, "big.pdf")

        assert response.status_code == 422
        assert response.json()["code"] == "invalid_file"

    def test_magic_mismatch_rejected(self, client: TestClient, conversation_id: UUID) -> None:
        response = _upload(client, conversation_id, b"not a pdf at all", "fake.pdf")

        assert response.status_code == 422
        assert response.json()["code"] == "invalid_file"

    def test_unknown_conversation_returns_404(self, client: TestClient) -> None:
        unknown = UUID("6f0c1c2e-3b4a-4c5d-8e9f-0a1b2c3d4e5f")
        response = _upload(client, unknown, PDF_BYTES, "payslip.pdf")

        assert response.status_code == 404


class TestUploadWithNoReader:
    def test_no_reader_returns_502(
        self, app_settings: Settings, sqlite_startup: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import car_advisor.api.app as app_module

        # Don't patch start_services — reader stays None.
        app = create_app(app_settings)
        with TestClient(app) as tc:
            cid = UUID(tc.post("/api/conversations").json()["id"])
            response = _upload(tc, cid, PDF_BYTES, "payslip.pdf")

        assert response.status_code == 502
        assert response.json()["code"] == "reader_failed"


class TestUploadNothingDetected:
    def test_nothing_detected_returns_422(
        self,
        app_settings: Settings,
        sqlite_startup: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import car_advisor.api.app as app_module

        reader = build_reader(FunctionModel(_empty_model))
        original_start = app_module.start_services

        def patched_start(settings: Settings, config: Any, engine: Any) -> Services:
            services = original_start(settings, config, engine)
            return Services(
                settings=services.settings,
                config=services.config,
                sessions=services.sessions,
                lookup=services.lookup,
                conversations=services.conversations,
                runner=services.runner,
                catalog_warnings=services.catalog_warnings,
                reader=reader,
            )

        monkeypatch.setattr(app_module, "start_services", patched_start)
        app = create_app(app_settings)
        with TestClient(app) as tc:
            cid = UUID(tc.post("/api/conversations").json()["id"])
            response = _upload(tc, cid, PDF_BYTES, "payslip.pdf")

        assert response.status_code == 422
        assert response.json()["code"] == "nothing_detected"


class TestConfirm:
    def test_confirm_saves_valid_fields(
        self, client: TestClient, conversation_id: UUID
    ) -> None:
        response = client.post(
            f"/api/conversations/{conversation_id}/documents/confirm",
            json={"changes": {"base_salary": 15000}},
        )

        assert response.status_code == 200
        profile = response.json()["profile"]
        assert profile["base_salary"] == "15000"

    def test_confirm_with_empty_changes_leaves_profile_unchanged(
        self, client: TestClient, conversation_id: UUID
    ) -> None:
        response = client.post(
            f"/api/conversations/{conversation_id}/documents/confirm",
            json={"changes": {}},
        )

        assert response.status_code == 200

    def test_confirm_invalid_field_returns_422(
        self, client: TestClient, conversation_id: UUID
    ) -> None:
        response = client.post(
            f"/api/conversations/{conversation_id}/documents/confirm",
            json={"changes": {"base_salary": -100}},
        )

        assert response.status_code == 422
