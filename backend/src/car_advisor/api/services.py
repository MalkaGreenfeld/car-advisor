"""The services that the routes share. The lifespan of the app builds them once at startup."""

from dataclasses import dataclass
from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.agent.runner import AgentRunner
from car_advisor.conversations.service import ConversationService
from car_advisor.dataset.lookup import CarLookup, UsageValueMismatch
from car_advisor.rules.loader import AppConfig
from car_advisor.settings import Settings


@dataclass(frozen=True)
class Services:
    settings: Settings
    config: AppConfig
    sessions: sessionmaker[Session]
    lookup: CarLookup
    conversations: ConversationService
    runner: AgentRunner
    # The catalog cars whose usage value differs from the dataset, found at startup (Req 4.19).
    catalog_warnings: tuple[UsageValueMismatch, ...] = ()

    @property
    def agent_available(self) -> bool:
        return self.runner.agent_available


async def get_services(request: Request) -> Services:
    """FastAPI dependency: the services that the lifespan stored on the app.

    It is async although it never waits. A plain def would run in the thread pool of the sync
    routes, where the health route could queue behind calls to a database that hangs (Req 14.9).
    """
    return cast(Services, request.app.state.services)
