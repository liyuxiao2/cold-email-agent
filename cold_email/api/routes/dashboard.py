import pathlib

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cold_email.database import Lead, get_async_session

TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_index(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(
        select(Lead)
        .where(Lead.status == "drafted")
        .options(selectinload(Lead.drafts))
        .order_by(Lead.created_at.desc())
    )
    leads = result.scalars().all()

    rows = []
    for lead in leads:
        # Pick the highest-version draft for display
        draft = max(lead.drafts, key=lambda d: d.version, default=None)
        if draft:
            rows.append({"lead": lead, "draft": draft})

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"rows": rows},
    )


@router.post("/leads/{lead_id}/approve")
async def approve_lead(
    lead_id: str,
    session: AsyncSession = Depends(get_async_session),
):
    lead = await session.get(Lead, lead_id)
    if lead:
        lead.status = "approved"
        await session.commit()
        from cold_email.workers.logistics import logistics_task

        logistics_task.delay(lead_id)
    return RedirectResponse(url="/", status_code=303)


@router.post("/leads/{lead_id}/reject")
async def reject_lead(
    lead_id: str,
    notes: str = Form(default=""),
    session: AsyncSession = Depends(get_async_session),
):
    lead = await session.get(Lead, lead_id)
    if lead:
        lead.status = "rejected"
        lead.error_msg = notes or None
        await session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/leads/{lead_id}/regenerate")
async def regenerate_lead(
    lead_id: str,
    session: AsyncSession = Depends(get_async_session),
):
    lead = await session.get(Lead, lead_id)
    if lead:
        # Step back to 'researched' so drafting_task re-runs from scratch
        lead.status = "researched"
        await session.commit()
        from cold_email.workers.drafting import drafting_task

        drafting_task.delay(lead_id)
    return RedirectResponse(url="/", status_code=303)
