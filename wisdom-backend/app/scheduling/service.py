"""Room scheduling service — conflict detection, recurring bookings, availability."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from dateutil.rrule import rrulestr
from sqlalchemy import and_, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.scheduling.models import MaintenanceWindow, Room, RoomBooking

logger = logging.getLogger(__name__)

# ── Color map for FullCalendar events ────────────────────────────────────────

BOOKING_COLORS = {
    "therapy": "#7C3AED",
    "workshop": "#2563EB",
    "training": "#0D9488",
    "maintenance": "#DC2626",
    "group": "#D97706",
}
DEFAULT_COLOR = "#6B7280"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _room_to_dict(room: Room) -> dict:
    return {
        "id": room.id,
        "name": room.name,
        "room_type": room.room_type,
        "capacity": room.capacity,
        "floor": room.floor,
        "description": room.description,
        "equipment": room.equipment,
        "is_active": room.is_active,
        "prep_time_minutes": room.prep_time_minutes,
        "notes": room.notes,
    }


def _booking_to_dict(booking: RoomBooking, room_name: Optional[str] = None) -> dict:
    return {
        "id": booking.id,
        "room_id": booking.room_id,
        "booked_by": booking.booked_by,
        "booking_type": booking.booking_type,
        "title": booking.title,
        "description": booking.description,
        "case_id": booking.case_id,
        "start_datetime": booking.start_datetime,
        "end_datetime": booking.end_datetime,
        "recurrence_rule": booking.recurrence_rule,
        "parent_booking_id": booking.parent_booking_id,
        "status": booking.status,
        "staff_ids": booking.staff_ids,
        "created_at": booking.created_at,
        "updated_at": booking.updated_at,
        "room_name": room_name,
    }


def _maintenance_to_dict(mw: MaintenanceWindow) -> dict:
    return {
        "id": mw.id,
        "room_id": mw.room_id,
        "start_datetime": mw.start_datetime,
        "end_datetime": mw.end_datetime,
        "reason": mw.reason,
        "created_by": mw.created_by,
    }


# ── 1. Conflict detection ───────────────────────────────────────────────────

async def check_conflict(
    room_id: UUID,
    start: datetime,
    end: datetime,
    exclude_id: Optional[UUID],
    db: AsyncSession,
) -> dict:
    """Check whether the given time range conflicts with existing bookings or
    maintenance windows in the specified room.  Prep time is factored in by
    expanding the query window backwards by the room's prep_time_minutes."""

    # Fetch room to get prep time
    room_result = await db.execute(select(Room).where(Room.id == room_id))
    room = room_result.scalar_one_or_none()
    prep_minutes = room.prep_time_minutes if room else 0
    adjusted_start = start - timedelta(minutes=prep_minutes)

    # Overlapping confirmed bookings
    booking_query = select(RoomBooking).where(
        and_(
            RoomBooking.room_id == room_id,
            RoomBooking.status == "confirmed",
            not_(
                or_(
                    RoomBooking.end_datetime <= adjusted_start,
                    RoomBooking.start_datetime >= end,
                )
            ),
        )
    )
    if exclude_id:
        booking_query = booking_query.where(RoomBooking.id != exclude_id)

    booking_result = await db.execute(booking_query)
    conflicting_bookings = booking_result.scalars().all()

    # Overlapping maintenance windows
    mw_query = select(MaintenanceWindow).where(
        and_(
            MaintenanceWindow.room_id == room_id,
            not_(
                or_(
                    MaintenanceWindow.end_datetime <= adjusted_start,
                    MaintenanceWindow.start_datetime >= end,
                )
            ),
        )
    )
    mw_result = await db.execute(mw_query)
    conflicting_mw = mw_result.scalars().all()

    conflicts: list[dict] = []
    for b in conflicting_bookings:
        conflicts.append({
            "id": str(b.id),
            "title": b.title,
            "start": b.start_datetime.isoformat(),
            "end": b.end_datetime.isoformat(),
            "type": "booking",
            "booking_type": b.booking_type,
        })
    for m in conflicting_mw:
        conflicts.append({
            "id": str(m.id),
            "title": f"Maintenance: {m.reason or 'Scheduled maintenance'}",
            "start": m.start_datetime.isoformat(),
            "end": m.end_datetime.isoformat(),
            "type": "maintenance",
        })

    return {"conflict": len(conflicts) > 0, "conflicting_bookings": conflicts}


# ── 2. Room CRUD ─────────────────────────────────────────────────────────────

async def list_rooms(db: AsyncSession, active_only: bool = True) -> list[dict]:
    """Return all rooms, optionally filtered to active-only."""
    query = select(Room).order_by(Room.name)
    if active_only:
        query = query.where(Room.is_active == True)  # noqa: E712
    result = await db.execute(query)
    return [_room_to_dict(r) for r in result.scalars().all()]


async def get_room(room_id: UUID, db: AsyncSession) -> Optional[dict]:
    """Return a single room dict or None."""
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if not room:
        return None
    return _room_to_dict(room)


async def update_room(room_id: UUID, data: dict, db: AsyncSession) -> Optional[dict]:
    """Update room fields and return the updated room dict."""
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if not room:
        return None

    for field, value in data.items():
        if value is not None and hasattr(room, field):
            setattr(room, field, value)

    await db.commit()
    await db.refresh(room)
    return _room_to_dict(room)


# ── 3. Room availability ────────────────────────────────────────────────────

async def get_room_availability(
    room_id: UUID,
    start: datetime,
    end: datetime,
    db: AsyncSession,
) -> list[dict]:
    """Generate a list of busy/free slots between *start* and *end* for the
    specified room.  Busy slots come from confirmed bookings and maintenance
    windows; free slots fill the gaps."""

    # Collect all busy intervals
    booking_result = await db.execute(
        select(RoomBooking).where(
            and_(
                RoomBooking.room_id == room_id,
                RoomBooking.status == "confirmed",
                not_(
                    or_(
                        RoomBooking.end_datetime <= start,
                        RoomBooking.start_datetime >= end,
                    )
                ),
            )
        ).order_by(RoomBooking.start_datetime)
    )
    bookings = booking_result.scalars().all()

    mw_result = await db.execute(
        select(MaintenanceWindow).where(
            and_(
                MaintenanceWindow.room_id == room_id,
                not_(
                    or_(
                        MaintenanceWindow.end_datetime <= start,
                        MaintenanceWindow.start_datetime >= end,
                    )
                ),
            )
        ).order_by(MaintenanceWindow.start_datetime)
    )
    maintenance = mw_result.scalars().all()

    # Merge into a sorted list of (start, end, booking_id, title)
    busy_intervals: list[tuple[datetime, datetime, Optional[UUID], Optional[str]]] = []
    for b in bookings:
        busy_intervals.append((
            max(b.start_datetime, start),
            min(b.end_datetime, end),
            b.id,
            b.title,
        ))
    for m in maintenance:
        busy_intervals.append((
            max(m.start_datetime, start),
            min(m.end_datetime, end),
            None,
            f"Maintenance: {m.reason or 'Scheduled'}",
        ))

    busy_intervals.sort(key=lambda x: x[0])

    # Build slots
    slots: list[dict] = []
    cursor = start

    for busy_start, busy_end, booking_id, title in busy_intervals:
        if busy_start > cursor:
            slots.append({
                "start": cursor,
                "end": busy_start,
                "status": "free",
                "booking_id": None,
                "title": None,
            })
        slots.append({
            "start": busy_start,
            "end": busy_end,
            "status": "busy",
            "booking_id": booking_id,
            "title": title,
        })
        if busy_end > cursor:
            cursor = busy_end

    if cursor < end:
        slots.append({
            "start": cursor,
            "end": end,
            "status": "free",
            "booking_id": None,
            "title": None,
        })

    return slots


# ── 4. Calendar events ──────────────────────────────────────────────────────

async def get_calendar_events(
    start: datetime,
    end: datetime,
    room_id: Optional[UUID],
    db: AsyncSession,
) -> list[dict]:
    """Return bookings and maintenance windows formatted for FullCalendar."""

    # Bookings
    booking_query = select(RoomBooking).where(
        and_(
            RoomBooking.status == "confirmed",
            not_(
                or_(
                    RoomBooking.end_datetime <= start,
                    RoomBooking.start_datetime >= end,
                )
            ),
        )
    )
    if room_id:
        booking_query = booking_query.where(RoomBooking.room_id == room_id)
    booking_query = booking_query.order_by(RoomBooking.start_datetime)

    booking_result = await db.execute(booking_query)
    bookings = booking_result.scalars().all()

    # Maintenance windows
    mw_query = select(MaintenanceWindow).where(
        not_(
            or_(
                MaintenanceWindow.end_datetime <= start,
                MaintenanceWindow.start_datetime >= end,
            )
        )
    )
    if room_id:
        mw_query = mw_query.where(MaintenanceWindow.room_id == room_id)
    mw_query = mw_query.order_by(MaintenanceWindow.start_datetime)

    mw_result = await db.execute(mw_query)
    maintenance = mw_result.scalars().all()

    events: list[dict] = []

    for b in bookings:
        events.append({
            "id": str(b.id),
            "title": b.title,
            "start": b.start_datetime.isoformat(),
            "end": b.end_datetime.isoformat(),
            "color": BOOKING_COLORS.get(b.booking_type, DEFAULT_COLOR),
            "extendedProps": {
                "type": "booking",
                "booking_type": b.booking_type,
                "room_id": str(b.room_id),
                "booked_by": str(b.booked_by),
                "case_id": str(b.case_id) if b.case_id else None,
                "status": b.status,
            },
        })

    for m in maintenance:
        events.append({
            "id": f"mw-{m.id}",
            "title": f"Maintenance: {m.reason or 'Scheduled'}",
            "start": m.start_datetime.isoformat(),
            "end": m.end_datetime.isoformat(),
            "color": BOOKING_COLORS["maintenance"],
            "extendedProps": {
                "type": "maintenance",
                "room_id": str(m.room_id),
                "reason": m.reason,
            },
        })

    return events


# ── 5. Booking CRUD ──────────────────────────────────────────────────────────

async def create_booking(
    data: dict,
    user_id: UUID,
    db: AsyncSession,
) -> list[dict]:
    """Create a booking (or recurring series).  Returns a list of created
    booking dicts.  Raises ValueError on conflict."""

    room_id = data["room_id"]
    start_dt = data["start_datetime"]
    end_dt = data["end_datetime"]
    recurrence_rule = data.get("recurrence_rule")

    # Fetch room name for response
    room_result = await db.execute(select(Room).where(Room.id == room_id))
    room = room_result.scalar_one_or_none()
    room_name = room.name if room else None

    # Build list of (start, end) for each occurrence
    if recurrence_rule:
        rule = rrulestr(recurrence_rule, dtstart=start_dt)
        duration = end_dt - start_dt
        occurrences = list(rule.between(
            start_dt,
            start_dt + timedelta(days=90),
            inc=True,
        ))
        if not occurrences:
            occurrences = [start_dt]
        time_slots = [(occ, occ + duration) for occ in occurrences]
    else:
        time_slots = [(start_dt, end_dt)]

    # Check conflicts for ALL instances
    for slot_start, slot_end in time_slots:
        conflict_result = await check_conflict(room_id, slot_start, slot_end, None, db)
        if conflict_result["conflict"]:
            raise ValueError(
                f"Conflict detected for slot {slot_start.isoformat()} - "
                f"{slot_end.isoformat()}: "
                f"{conflict_result['conflicting_bookings']}"
            )

    # Prepare staff_ids as JSONB-compatible value
    staff_ids_value = None
    if data.get("staff_ids"):
        staff_ids_value = {"ids": [str(sid) for sid in data["staff_ids"]]}

    # Create bookings
    created: list[dict] = []
    parent_id: Optional[UUID] = None

    for idx, (slot_start, slot_end) in enumerate(time_slots):
        booking = RoomBooking(
            room_id=room_id,
            booked_by=user_id,
            booking_type=data["booking_type"],
            title=data["title"],
            description=data.get("description"),
            case_id=data.get("case_id"),
            start_datetime=slot_start,
            end_datetime=slot_end,
            recurrence_rule=recurrence_rule if idx == 0 else None,
            parent_booking_id=parent_id,
            status="confirmed",
            staff_ids=staff_ids_value,
        )
        db.add(booking)
        await db.flush()

        # First booking in series is the parent
        if idx == 0 and recurrence_rule:
            parent_id = booking.id

        created.append(_booking_to_dict(booking, room_name))

    await db.commit()

    # Refresh all to get server defaults
    refreshed: list[dict] = []
    for item in created:
        result = await db.execute(
            select(RoomBooking).where(RoomBooking.id == item["id"])
        )
        b = result.scalar_one()
        refreshed.append(_booking_to_dict(b, room_name))

    return refreshed


async def get_booking(booking_id: UUID, db: AsyncSession) -> Optional[dict]:
    """Return a single booking dict with room_name, or None."""
    result = await db.execute(
        select(RoomBooking).where(RoomBooking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        return None

    # Fetch room name
    room_result = await db.execute(select(Room).where(Room.id == booking.room_id))
    room = room_result.scalar_one_or_none()
    room_name = room.name if room else None

    return _booking_to_dict(booking, room_name)


async def update_booking(
    booking_id: UUID,
    data: dict,
    db: AsyncSession,
) -> Optional[dict]:
    """Update an existing booking. If time is changed, re-check conflicts.
    Returns updated booking dict or None if not found.
    Raises ValueError on conflict."""

    result = await db.execute(
        select(RoomBooking).where(RoomBooking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        return None

    # Determine effective start/end for conflict checking
    new_start = data.get("start_datetime", booking.start_datetime)
    new_end = data.get("end_datetime", booking.end_datetime)

    time_changed = (
        data.get("start_datetime") is not None
        or data.get("end_datetime") is not None
    )

    if time_changed:
        conflict_result = await check_conflict(
            booking.room_id, new_start, new_end, booking_id, db
        )
        if conflict_result["conflict"]:
            raise ValueError(
                f"Conflict detected: {conflict_result['conflicting_bookings']}"
            )

    # Apply updates
    for field, value in data.items():
        if value is not None and hasattr(booking, field):
            if field == "staff_ids" and isinstance(value, list):
                setattr(booking, field, {"ids": [str(sid) for sid in value]})
            else:
                setattr(booking, field, value)

    await db.commit()
    await db.refresh(booking)

    # Fetch room name
    room_result = await db.execute(select(Room).where(Room.id == booking.room_id))
    room = room_result.scalar_one_or_none()
    room_name = room.name if room else None

    return _booking_to_dict(booking, room_name)


async def cancel_booking(
    booking_id: UUID,
    cancel_series: bool,
    db: AsyncSession,
) -> Optional[dict]:
    """Cancel a booking.  If cancel_series is True and the booking belongs to a
    recurring series, cancel all future instances as well.  Returns the
    cancelled booking dict or None if not found."""

    result = await db.execute(
        select(RoomBooking).where(RoomBooking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        return None

    now = datetime.now(timezone.utc)
    booking.status = "cancelled"

    if cancel_series:
        # Determine the series parent id
        series_parent_id = booking.parent_booking_id or booking.id

        # Cancel all future instances in this series
        series_query = select(RoomBooking).where(
            and_(
                or_(
                    RoomBooking.parent_booking_id == series_parent_id,
                    RoomBooking.id == series_parent_id,
                ),
                RoomBooking.start_datetime >= now,
                RoomBooking.status == "confirmed",
            )
        )
        series_result = await db.execute(series_query)
        for b in series_result.scalars().all():
            b.status = "cancelled"

    await db.commit()
    await db.refresh(booking)

    # Fetch room name
    room_result = await db.execute(select(Room).where(Room.id == booking.room_id))
    room = room_result.scalar_one_or_none()
    room_name = room.name if room else None

    return _booking_to_dict(booking, room_name)


# ── 6. Maintenance windows ──────────────────────────────────────────────────

async def create_maintenance_window(
    room_id: UUID,
    data: dict,
    user_id: UUID,
    db: AsyncSession,
) -> dict:
    """Create a maintenance window for the given room."""
    mw = MaintenanceWindow(
        room_id=room_id,
        start_datetime=data["start_datetime"],
        end_datetime=data["end_datetime"],
        reason=data.get("reason"),
        created_by=user_id,
    )
    db.add(mw)
    await db.commit()
    await db.refresh(mw)
    return _maintenance_to_dict(mw)


async def list_maintenance_windows(
    room_id: UUID,
    db: AsyncSession,
) -> list[dict]:
    """List all maintenance windows for a room, ordered by start time."""
    result = await db.execute(
        select(MaintenanceWindow)
        .where(MaintenanceWindow.room_id == room_id)
        .order_by(MaintenanceWindow.start_datetime)
    )
    return [_maintenance_to_dict(mw) for mw in result.scalars().all()]


async def delete_maintenance_window(
    maintenance_id: UUID,
    db: AsyncSession,
) -> Optional[dict]:
    """Delete a maintenance window.  Returns the deleted window dict or None."""
    result = await db.execute(
        select(MaintenanceWindow).where(MaintenanceWindow.id == maintenance_id)
    )
    mw = result.scalar_one_or_none()
    if not mw:
        return None

    data = _maintenance_to_dict(mw)
    await db.delete(mw)
    await db.commit()
    return data
