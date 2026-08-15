"""Per-user send cadence.

Validation happens HERE, not in the worker. An unresolvable timezone accepted at
this boundary would make next_slot raise inside a Celery worker, turning a form
typo into a background failure the user cannot connect to their action.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.deps import get_current_user
from cold_email.cadence import CadenceInvalid, validate_cadence
from cold_email.database import User, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cadence", tags=["cadence"])


@router.get("")
async def get_cadence(user: User = Depends(get_current_user)):
    """The caller's cadence, or null (meaning send immediately on approve)."""
    return {"cadence": user.send_cadence}


@router.put("")
async def put_cadence(
    payload: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Validate and save a cadence."""
    try:
        normalized = validate_cadence(payload)
    except CadenceInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user.send_cadence = normalized
    await session.commit()
    return {"cadence": normalized}


@router.delete("")
async def delete_cadence(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Revert to sending immediately on approve."""
    user.send_cadence = None
    await session.commit()
    return {"cadence": None}
