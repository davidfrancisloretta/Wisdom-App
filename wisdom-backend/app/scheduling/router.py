"""Scheduling API router — rooms, bookings, maintenance, calendar."""

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit_service import log_event
from app.auth.guards import get_current_user, require_admin, require_role
from app.auth.models import User
from app.database import get_db
from app.scheduling import service
from app.scheduling.schemas import (
    AvailabilitySlot,
    BookingCreate,
    BookingOut,
    BookingUpdate,
    CalendarEvent,
    ConflictCheckRequest,
    ConflictCheckResponse,
    MaintenanceCreate,
    MaintenanceOut,
    RoomOut,
    RoomUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Rooms ────────────────────────────────────────────────────────────────────


@router.get("/rooms", response_model=list[RoomOut])
async def list_rooms(
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all rooms, optionally filtered to active-only."""
    rooms = await service.list_rooms(db, active_only=active_only)
    return rooms


@router.get("/rooms/{room_id}", response_model=RoomOut)
async def get_room(
    room_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a single room by ID."""
    room = await service.get_room(room_id, db)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return room


@router.put("/rooms/{room_id}", response_model=RoomOut)
async def update_room(
    room_id: UUID,
    body: RoomUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("super_admin", "admin", "staff")),
):
    """Update a room (admin/staff only)."""
    old_room = await service.get_room(room_id, db)
    if not old_room:
        raise HTTPException(status_code=404, detail="Room not found")

    update_data = body.model_dump(exclude_unset=True)
    updated = await service.update_room(room_id, update_data, db)

    await log_event(
        user_id=user.id,
        action="room.update",
        resource_type="room",
        resource_id=str(room_id),
        old_values=old_room,
        new_values=updated,
        request=request,
        db=db,
    )

    return updated


# ── Room availability ────────────────────────────────────────────────────────


@router.get("/rooms/{room_id}/availability", response_model=list[AvailabilitySlot])
async def get_availability(
    room_id: UUID,
    start: datetime = Query(..., description="Start of range (ISO 8601)"),
    end: datetime = Query(..., description="End of range (ISO 8601)"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get availability slots for a room in a given time range."""
    room = await service.get_room(room_id, db)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    slots = await service.get_room_availability(room_id, start, end, db)
    return slots


# ── Calendar ─────────────────────────────────────────────────────────────────


@router.get("/calendar", response_model=list[CalendarEvent])
async def get_calendar(
    start: datetime = Query(..., description="Start of range (ISO 8601)"),
    end: datetime = Query(..., description="End of range (ISO 8601)"),
    room_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get calendar events (bookings + maintenance) for FullCalendar."""
    events = await service.get_calendar_events(start, end, room_id, db)
    return events


# ── Bookings ─────────────────────────────────────────────────────────────────


@router.post("/bookings", response_model=list[BookingOut], status_code=201)
async def create_booking(
    body: BookingCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new booking (or recurring series)."""
    data = body.model_dump()

    try:
        created = await service.create_booking(data, user.id, db)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    await log_event(
        user_id=user.id,
        action="booking.create",
        resource_type="booking",
        resource_id=str(created[0]["id"]) if created else None,
        old_values=None,
        new_values={"count": len(created), "title": data["title"]},
        request=request,
        db=db,
    )

    return created


@router.get("/bookings/{booking_id}", response_model=BookingOut)
async def get_booking(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a single booking by ID."""
    booking = await service.get_booking(booking_id, db)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking


@router.put("/bookings/{booking_id}", response_model=BookingOut)
async def update_booking(
    booking_id: UUID,
    body: BookingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update an existing booking."""
    old_booking = await service.get_booking(booking_id, db)
    if not old_booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    update_data = body.model_dump(exclude_unset=True)

    try:
        updated = await service.update_booking(booking_id, update_data, db)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    await log_event(
        user_id=user.id,
        action="booking.update",
        resource_type="booking",
        resource_id=str(booking_id),
        old_values=old_booking,
        new_values=updated,
        request=request,
        db=db,
    )

    return updated


@router.delete("/bookings/{booking_id}", response_model=BookingOut)
async def cancel_booking(
    booking_id: UUID,
    request: Request,
    cancel_series: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cancel a booking. Optionally cancel the entire recurring series."""
    booking = await service.cancel_booking(booking_id, cancel_series, db)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    await log_event(
        user_id=user.id,
        action="booking.cancel",
        resource_type="booking",
        resource_id=str(booking_id),
        old_values=None,
        new_values={"cancel_series": cancel_series},
        request=request,
        db=db,
    )

    return booking


# ── Conflict check ───────────────────────────────────────────────────────────


@router.post("/bookings/check-conflict", response_model=ConflictCheckResponse)
async def check_conflict(
    body: ConflictCheckRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Check whether a proposed booking conflicts with existing bookings."""
    result = await service.check_conflict(
        room_id=body.room_id,
        start=body.start_datetime,
        end=body.end_datetime,
        exclude_id=body.exclude_booking_id,
        db=db,
    )
    return result


# ── Maintenance windows ─────────────────────────────────────────────────────


@router.post(
    "/rooms/{room_id}/maintenance",
    response_model=MaintenanceOut,
    status_code=201,
)
async def create_maintenance(
    room_id: UUID,
    body: MaintenanceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Create a maintenance window for a room (admin only)."""
    room = await service.get_room(room_id, db)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    data = body.model_dump()
    mw = await service.create_maintenance_window(room_id, data, user.id, db)

    await log_event(
        user_id=user.id,
        action="maintenance.create",
        resource_type="maintenance_window",
        resource_id=str(mw["id"]),
        old_values=None,
        new_values=mw,
        request=request,
        db=db,
    )

    return mw


@router.get("/rooms/{room_id}/maintenance", response_model=list[MaintenanceOut])
async def list_maintenance(
    room_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List maintenance windows for a room."""
    room = await service.get_room(room_id, db)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    windows = await service.list_maintenance_windows(room_id, db)
    return windows


@router.delete("/maintenance/{maintenance_id}", response_model=MaintenanceOut)
async def delete_maintenance(
    maintenance_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Delete a maintenance window (admin only)."""
    deleted = await service.delete_maintenance_window(maintenance_id, db)
    if not deleted:
        raise HTTPException(status_code=404, detail="Maintenance window not found")

    await log_event(
        user_id=user.id,
        action="maintenance.delete",
        resource_type="maintenance_window",
        resource_id=str(maintenance_id),
        old_values=deleted,
        new_values=None,
        request=request,
        db=db,
    )

    return deleted
