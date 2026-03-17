"""Analytics dashboard API endpoints."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import get_current_user, require_role
from app.auth.models import User
from app.database import get_db

from app.analytics import service

router = APIRouter()


def _parse_range(
    start: Optional[datetime], end: Optional[datetime]
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Pass through query params; service layer handles defaults."""
    return start, end


# ------------------------------------------------------------------
# GET /overview
# ------------------------------------------------------------------

@router.get("/overview")
async def overview(
    start: Optional[datetime] = Query(None, description="Period start (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="Period end (ISO 8601)"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    s, e = _parse_range(start, end)
    return await service.get_overview(db, start=s, end=e)


# ------------------------------------------------------------------
# GET /cases
# ------------------------------------------------------------------

@router.get("/cases")
async def cases(
    start: Optional[datetime] = Query(None, description="Period start (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="Period end (ISO 8601)"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    s, e = _parse_range(start, end)
    return await service.get_case_volume(db, start=s, end=e)


# ------------------------------------------------------------------
# GET /assessments
# ------------------------------------------------------------------

@router.get("/assessments")
async def assessments(
    start: Optional[datetime] = Query(None, description="Period start (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="Period end (ISO 8601)"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    s, e = _parse_range(start, end)
    return await service.get_assessment_trends(db, start=s, end=e)


# ------------------------------------------------------------------
# GET /rooms
# ------------------------------------------------------------------

@router.get("/rooms")
async def rooms(
    start: Optional[datetime] = Query(None, description="Period start (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="Period end (ISO 8601)"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    s, e = _parse_range(start, end)
    return await service.get_room_utilisation(db, start=s, end=e)


# ------------------------------------------------------------------
# GET /staff — restricted to chief_therapist or above
# ------------------------------------------------------------------

@router.get("/staff")
async def staff(
    start: Optional[datetime] = Query(None, description="Period start (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="Period end (ISO 8601)"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("super_admin", "admin", "chief_therapist")),
):
    s, e = _parse_range(start, end)
    return await service.get_staff_activity(db, start=s, end=e)


# ------------------------------------------------------------------
# GET /donations
# ------------------------------------------------------------------

@router.get("/donations")
async def donations(
    start: Optional[datetime] = Query(None, description="Period start (ISO 8601)"),
    end: Optional[datetime] = Query(None, description="Period end (ISO 8601)"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    s, e = _parse_range(start, end)
    return await service.get_donation_analytics(db, start=s, end=e)


# ------------------------------------------------------------------
# GET /outcomes
# ------------------------------------------------------------------

@router.get("/outcomes")
async def outcomes(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return await service.get_program_effectiveness(db)
