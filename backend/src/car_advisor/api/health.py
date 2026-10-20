"""The health route: whether the database answers and whether the dataset is loaded (Req 14.9).

The agent is not part of the check, so an app without an API key can still report healthy
(Req 14.5).
"""

import logging
from asyncio import to_thread, wait_for
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from car_advisor.api import API_PREFIX
from car_advisor.api.schemas import Health
from car_advisor.api.services import Services, get_services
from car_advisor.dataset.models import UsageValueRow

# The route answers within 5 s (Req 14.9). The check gets 3 s of them, which leaves the rest for
# the response even when the database never replies.
CHECK_TIMEOUT: Final = 3.0

router = APIRouter(prefix=API_PREFIX)
logger = logging.getLogger(__name__)


def _count_records(sessions: sessionmaker[Session], tax_year: int) -> int:
    query = (
        select(func.count()).select_from(UsageValueRow).where(UsageValueRow.tax_year == tax_year)
    )
    with sessions() as session:
        return session.execute(query).scalar_one()


async def _check_health(services: Services) -> Health:
    tax_year = services.settings.tax_year
    try:
        # A worker thread cannot be stopped. On a timeout, wait_for only stops waiting for it; the
        # thread closes its session when the database answers or gives up.
        count = await wait_for(
            to_thread(_count_records, services.sessions, tax_year), CHECK_TIMEOUT
        )
    except Exception as error:
        # The type only: the message of a database error can quote the SQL parameters.
        logger.error("health_check_failed", extra={"error_type": type(error).__name__})
        return Health(
            database_reachable=False, dataset_loaded=False, tax_year=tax_year, record_count=0
        )
    return Health(
        database_reachable=True, dataset_loaded=count > 0, tax_year=tax_year, record_count=count
    )


@router.get("/health", responses={503: {"model": Health}})
async def get_health(
    services: Annotated[Services, Depends(get_services)], response: Response
) -> Health:
    health = await _check_health(services)
    if not (health.database_reachable and health.dataset_loaded):
        response.status_code = 503
    return health
