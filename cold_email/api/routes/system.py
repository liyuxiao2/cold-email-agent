import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.database import Lead, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check(session: AsyncSession = Depends(get_async_session)):
    """Health check verifying API and Database connectivity."""
    try:
        await session.execute(select(func.count()).select_from(Lead))
        db_status = "connected"
    except Exception as e:
        logger.error(f"Health check DB error: {e}")
        db_status = f"unhealthy: {e}"

    return {"status": "ok", "database": db_status}
