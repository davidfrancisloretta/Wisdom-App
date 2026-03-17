"""Scheduling Pydantic schemas."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RoomOut(BaseModel):
    id: UUID
    name: str
    room_type: str
    capacity: int
    floor: Optional[str] = None
    description: Optional[str] = None
    equipment: Optional[dict] = None
    is_active: bool
    prep_time_minutes: int
    notes: Optional[str] = None

    model_config = {"from_attributes": True}


class RoomUpdate(BaseModel):
    name: Optional[str] = None
    room_type: Optional[str] = None
    capacity: Optional[int] = None
    floor: Optional[str] = None
    description: Optional[str] = None
    equipment: Optional[dict] = None
    is_active: Optional[bool] = None
    prep_time_minutes: Optional[int] = None
    notes: Optional[str] = None


class BookingCreate(BaseModel):
    room_id: UUID
    booking_type: str
    title: str
    description: Optional[str] = None
    case_id: Optional[UUID] = None
    start_datetime: datetime
    end_datetime: datetime
    recurrence_rule: Optional[str] = None
    staff_ids: Optional[list[UUID]] = None


class BookingUpdate(BaseModel):
    booking_type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    case_id: Optional[UUID] = None
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    staff_ids: Optional[list[UUID]] = None


class BookingOut(BaseModel):
    id: UUID
    room_id: UUID
    booked_by: UUID
    booking_type: str
    title: str
    description: Optional[str] = None
    case_id: Optional[UUID] = None
    start_datetime: datetime
    end_datetime: datetime
    recurrence_rule: Optional[str] = None
    parent_booking_id: Optional[UUID] = None
    status: str
    staff_ids: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    room_name: Optional[str] = None

    model_config = {"from_attributes": True}


class ConflictCheckRequest(BaseModel):
    room_id: UUID
    start_datetime: datetime
    end_datetime: datetime
    exclude_booking_id: Optional[UUID] = None


class ConflictCheckResponse(BaseModel):
    conflict: bool
    conflicting_bookings: list[dict] = []


class MaintenanceCreate(BaseModel):
    start_datetime: datetime
    end_datetime: datetime
    reason: Optional[str] = None


class MaintenanceOut(BaseModel):
    id: UUID
    room_id: UUID
    start_datetime: datetime
    end_datetime: datetime
    reason: Optional[str] = None
    created_by: Optional[UUID] = None

    model_config = {"from_attributes": True}


class CalendarEvent(BaseModel):
    id: str
    title: str
    start: str
    end: str
    color: Optional[str] = None
    extendedProps: Optional[dict] = None


class AvailabilitySlot(BaseModel):
    start: datetime
    end: datetime
    status: str  # "free" or "busy"
    booking_id: Optional[UUID] = None
    title: Optional[str] = None
