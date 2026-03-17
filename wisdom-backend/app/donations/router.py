"""Donation router — public and admin endpoints for campaigns and donations."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit_service import log_event
from app.auth.guards import get_current_user, require_admin
from app.auth.models import User
from app.database import get_db
from app.donations.schemas import (
    CampaignCreate,
    CampaignOut,
    CampaignUpdate,
    DonationOut,
    OneTimeDonation,
    RecurringDonation,
)
from app.donations.service import (
    create_campaign,
    create_one_time_donation,
    create_recurring_donation,
    generate_receipt_pdf,
    get_campaign,
    list_campaigns,
    list_donations_admin,
    update_campaign,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# Admin sub-router
# ---------------------------------------------------------------------------
admin_router = APIRouter(prefix="/admin", tags=["donations-admin"])


@admin_router.get("/all")
async def admin_list_donations(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    campaign_id: Optional[UUID] = None,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all donations (admin only)."""
    result = await list_donations_admin(db, page=page, page_size=page_size, campaign_id=campaign_id)
    return result


@admin_router.post("/campaigns", response_model=CampaignOut)
async def admin_create_campaign(
    data: CampaignCreate,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new donation campaign (admin only)."""
    result = await create_campaign(data, user.id, db)

    await log_event(
        user_id=user.id,
        action="create",
        resource_type="donation_campaign",
        resource_id=str(result["id"]),
        old_values=None,
        new_values={"title": result["title"], "goal_amount": result["goal_amount"]},
        request=request,
        db=db,
    )

    return result


@admin_router.put("/campaigns/{campaign_id}", response_model=CampaignOut)
async def admin_update_campaign(
    campaign_id: UUID,
    data: CampaignUpdate,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update a donation campaign (admin only)."""
    result = await update_campaign(campaign_id, data, db)
    if not result:
        raise HTTPException(status_code=404, detail="Campaign not found")

    await log_event(
        user_id=user.id,
        action="update",
        resource_type="donation_campaign",
        resource_id=str(campaign_id),
        old_values=None,
        new_values=data.model_dump(exclude_unset=True),
        request=request,
        db=db,
    )

    return result


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------

@router.get("/campaigns", response_model=list[CampaignOut])
async def public_list_campaigns(
    db: AsyncSession = Depends(get_db),
):
    """List active donation campaigns (public, no auth required)."""
    return await list_campaigns(db, active_only=True)


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def public_get_campaign(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get campaign detail with progress (public, no auth required)."""
    result = await get_campaign(campaign_id, db)
    if not result:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return result


@router.post("/one-time")
async def public_one_time_donation(
    data: OneTimeDonation,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Create a one-time donation (public, no auth required)."""
    result = await create_one_time_donation(data, db, background_tasks)
    return result


@router.post("/recurring")
async def public_recurring_donation(
    data: RecurringDonation,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Start a recurring donation (public, no auth required)."""
    result = await create_recurring_donation(data, db, background_tasks)
    return result


@router.get("/receipt/{donation_id}")
async def public_download_receipt(
    donation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Download donation receipt PDF (public by receipt ID)."""
    pdf_bytes = await generate_receipt_pdf(donation_id, db)
    if not pdf_bytes:
        raise HTTPException(status_code=404, detail="Donation not found")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=receipt_{str(donation_id)[:8]}.pdf"},
    )


# ---------------------------------------------------------------------------
# Mount admin sub-router
# ---------------------------------------------------------------------------
router.include_router(admin_router)
