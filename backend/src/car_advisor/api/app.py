"""The FastAPI app: the startup sequence in its lifespan, the error handlers and the middleware.

Importing this module builds `app` for uvicorn without reading the environment or the database.
App_Settings are loaded and the database is opened when the lifespan starts.
"""

import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from functools import partial

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr, ValidationError
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy import Engine
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.agent.advisor import build_advisor
from car_advisor.agent.runner import AgentBusy, AgentRunner
from car_advisor.agent.tools import AdvisorDeps
from car_advisor.api.conversations import router as conversations_router
from car_advisor.api.health import router as health_router
from car_advisor.api.rep import router as rep_router
from car_advisor.api.schemas import ErrorBody, ErrorCode
from car_advisor.api.services import Services
from car_advisor.conversations.profile import FieldError, ProfileValidationError
from car_advisor.conversations.service import (
    ConversationConflict,
    ConversationNotFound,
    ConversationService,
)
from car_advisor.dataset.importer import DatasetImportError, has_records, import_tax_year
from car_advisor.dataset.lookup import CarLookup, CatalogMismatch, check_catalog
from car_advisor.db import ensure_database, make_engine, make_session_factory, run_migrations
from car_advisor.logs import RequestLogMiddleware, configure_logging
from car_advisor.rules.loader import AppConfig, ConfigError, load_config
from car_advisor.settings import Settings, settings_error_fields

logger = logging.getLogger(__name__)


class SettingsError(Exception):
    """App_Settings are invalid. It names the variables and never a value."""

    def __init__(self, fields: Sequence[str]) -> None:
        super().__init__(f"invalid settings: {', '.join(fields)}")
        self.fields = list(fields)


def _failure_details(error: Exception) -> list[str]:
    # Only the app's own errors are described: their messages hold paths, names and rule
    # problems, and never a salary or a secret. Any other message could quote the SQL
    # statement and its parameters, or a connection string.
    match error:
        case SettingsError():
            return error.fields
        case ConfigError():
            return [str(error.path), *error.problems]
        case DatasetImportError():
            return [str(error)]
        case CatalogMismatch():
            return [f"{car.display_name}: {car.official_key}" for car in error.missing]
        case _:
            return []


@contextmanager
def _stage(name: str) -> Iterator[None]:
    """Log how a startup stage failed, with its name and the type of the error, and re-raise."""
    try:
        yield
    except Exception as error:
        line: dict[str, object] = {"stage": name, "error_type": type(error).__name__}
        if details := _failure_details(error):
            line["fields"] = details
        logger.error("startup_failed", extra=line)
        raise


def load_settings() -> Settings:
    with _stage("settings"):
        try:
            return Settings()
        except ValidationError as error:
            # The message of Pydantic quotes every input, the database password among them,
            # and uvicorn prints the exception that stops the startup.
            raise SettingsError(settings_error_fields(error)) from None


def load_app_config(settings: Settings) -> AppConfig:
    with _stage("config"):
        return load_config(settings.rules_path, settings.catalog_path, settings.occupations_path)


def open_database(settings: Settings) -> Engine:
    """Create the database if it is missing, bring its schema up to date and connect to it."""
    with _stage("create_database"):
        ensure_database(settings)
    engine = make_engine(settings)
    try:
        with _stage("migrate"):
            run_migrations(engine)
    except Exception:
        engine.dispose()
        raise
    return engine


def import_dataset_if_missing(sessions: sessionmaker[Session], settings: Settings) -> None:
    """Load the tax year of App_Settings, unless the database already holds records of it."""
    with _stage("import"):
        if has_records(sessions, settings.tax_year):
            logger.info("dataset_present", extra={"tax_year": settings.tax_year})
            return
        result = import_tax_year(sessions, settings.dataset_path, settings.tax_year)
    logger.info(
        "dataset_imported",
        extra={"tax_year": result.tax_year, "record_count": result.record_count},
    )


def build_model(settings: Settings, api_key: SecretStr) -> Model:
    """Build the language model with the API key of App_Settings, never one from the environment."""
    if settings.llm_provider != "openai":
        raise SettingsError(["LLM_PROVIDER: unsupported_provider"])
    provider = OpenAIProvider(api_key=api_key.get_secret_value())
    return OpenAIResponsesModel(settings.llm_model, provider=provider)


def build_agent(settings: Settings) -> Agent[AdvisorDeps, str] | None:
    """Build Advisor_Agent, or return None, with a warning, when App_Settings has no API key."""
    if settings.openai_api_key is None:
        logger.warning("agent_disabled", extra={"stage": "agent"})
        return None
    with _stage("agent"):
        return build_advisor(build_model(settings, settings.openai_api_key))


def start_services(settings: Settings, config: AppConfig, engine: Engine) -> Services:
    """Run the stages that need the database, then wire the services around it."""
    sessions = make_session_factory(engine)
    lookup = CarLookup(sessions, settings.tax_year)
    import_dataset_if_missing(sessions, settings)
    with _stage("catalog_check"):
        check_catalog(lookup, config.catalog)
    conversations = ConversationService(sessions, lookup, config)
    runner = AgentRunner(build_agent(settings), conversations, lookup, config)
    return Services(settings, config, sessions, lookup, conversations, runner)


@asynccontextmanager
async def _lifespan(app: FastAPI, settings: Settings | None) -> AsyncIterator[None]:
    """Run the startup sequence of the design. Any failure raises, so uvicorn exits non-zero."""
    configure_logging()
    resolved = load_settings() if settings is None else settings
    config = load_app_config(resolved)
    engine = open_database(resolved)
    try:
        app.state.services = start_services(resolved, config, engine)
        yield
    finally:
        engine.dispose()


def _error_response(
    status_code: int, code: ErrorCode, fields: Sequence[FieldError] = ()
) -> JSONResponse:
    body = ErrorBody(code=code, fields=tuple(fields) or None)
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code)


async def _conversation_not_found(request: Request, error: ConversationNotFound) -> JSONResponse:
    return _error_response(404, ErrorCode.CONVERSATION_NOT_FOUND)


async def _invalid_fields(request: Request, error: ProfileValidationError) -> JSONResponse:
    return _error_response(422, ErrorCode.INVALID_FIELDS, error.errors)


async def _agent_busy(request: Request, error: AgentBusy) -> JSONResponse:
    return _error_response(409, ErrorCode.AGENT_BUSY)


async def _conflict(request: Request, error: ConversationConflict) -> JSONResponse:
    return _error_response(409, ErrorCode.CONFLICT)


async def _database_unavailable(request: Request, error: Exception) -> JSONResponse:
    # The request line has the status; this one adds the type of the error, never its message,
    # which can quote the SQL statement with its parameters.
    logger.error("database_unavailable", extra={"error_type": type(error).__name__})
    return _error_response(503, ErrorCode.DATABASE_UNAVAILABLE)


def _add_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ConversationNotFound, _conversation_not_found)
    app.add_exception_handler(ProfileValidationError, _invalid_fields)
    app.add_exception_handler(AgentBusy, _agent_busy)
    app.add_exception_handler(ConversationConflict, _conflict)
    # A lost connection, a login that failed and a pool that ran out of connections.
    for error_type in (OperationalError, InterfaceError, PoolTimeout):
        app.add_exception_handler(error_type, _database_unavailable)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. It reads nothing until its lifespan starts, so building it is cheap.

    The settings come from the environment at startup unless the caller passes them in.
    """
    # The interactive docs pages load their scripts from a CDN, which Req 14.12 rules out.
    app = FastAPI(
        title="Strauss Car Advisor",
        lifespan=partial(_lifespan, settings=settings),
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(RequestLogMiddleware)
    _add_error_handlers(app)
    # Each router carries API_PREFIX itself, so the route template in the request log is the full
    # path. They reach the services through get_services.
    app.include_router(health_router)
    app.include_router(conversations_router)
    app.include_router(rep_router)
    return app


app = create_app()
