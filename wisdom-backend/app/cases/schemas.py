"""Pydantic schemas for child case management."""

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Case CRUD
# ---------------------------------------------------------------------------

class CaseCreate(BaseModel):
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    date_of_birth: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    gender: Optional[str] = None
    age_at_intake: Optional[int] = None
    guardian_name: Optional[str] = None
    guardian_phone: Optional[str] = None
    guardian_email: Optional[str] = None
    guardian_relationship: Optional[str] = None
    address: Optional[str] = None
    school_name: Optional[str] = None
    referral_source: Optional[str] = None
    presenting_issues: Optional[list[str]] = None
    initial_diagnosis: Optional[str] = None
    intake_date: Optional[date] = None


class CaseUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    age_at_intake: Optional[int] = None
    guardian_name: Optional[str] = None
    guardian_phone: Optional[str] = None
    guardian_email: Optional[str] = None
    guardian_relationship: Optional[str] = None
    address: Optional[str] = None
    school_name: Optional[str] = None
    referral_source: Optional[str] = None
    presenting_issues: Optional[list[str]] = None
    initial_diagnosis: Optional[str] = None
    status: Optional[str] = None
    intake_date: Optional[date] = None


class CaseOut(BaseModel):
    id: UUID
    case_number: str
    first_name: str
    last_name: str
    date_of_birth: str
    gender: Optional[str] = None
    age_at_intake: Optional[int] = None
    guardian_name: Optional[str] = None
    guardian_phone: Optional[str] = None
    guardian_email: Optional[str] = None
    guardian_relationship: Optional[str] = None
    address: Optional[str] = None
    school_name: Optional[str] = None
    referral_source: Optional[str] = None
    presenting_issues: Optional[list[str]] = None
    initial_diagnosis: Optional[str] = None
    status: str
    intake_date: Optional[date] = None
    closed_date: Optional[date] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CaseListItem(BaseModel):
    id: UUID
    case_number: str
    first_name: str
    last_name: str
    age_at_intake: Optional[int] = None
    status: str
    intake_date: Optional[date] = None
    created_at: datetime
    assigned_therapist: Optional[str] = None
    last_activity: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

class AssignmentCreate(BaseModel):
    user_id: UUID
    assignment_type: str = Field(pattern=r"^(primary_therapist|nurturer|supervisor)$")


class AssignmentOut(BaseModel):
    id: UUID
    case_id: UUID
    user_id: UUID
    assignment_type: str
    assigned_at: datetime
    assigned_by: Optional[UUID] = None
    is_active: bool
    user_name: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

class NoteCreate(BaseModel):
    note_type: str = Field(pattern=r"^(session|observation|intervention|progress|followup)$")
    content: str = Field(min_length=1)
    session_date: Optional[date] = None


class NoteUpdate(BaseModel):
    content: Optional[str] = None
    note_type: Optional[str] = None
    session_date: Optional[date] = None


class NoteOut(BaseModel):
    id: UUID
    case_id: UUID
    author_id: UUID
    note_type: str
    content: str
    session_date: Optional[date] = None
    created_at: datetime
    updated_at: datetime
    author_name: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Intervention Plans
# ---------------------------------------------------------------------------

class InterventionCreate(BaseModel):
    goals: Optional[list[dict]] = None
    strategies: Optional[list[dict]] = None
    review_date: Optional[date] = None


class InterventionUpdate(BaseModel):
    goals: Optional[list[dict]] = None
    strategies: Optional[list[dict]] = None
    review_date: Optional[date] = None
    status: Optional[str] = None


class InterventionOut(BaseModel):
    id: UUID
    case_id: UUID
    created_by: UUID
    goals: Optional[list[dict]] = None
    strategies: Optional[list[dict]] = None
    review_date: Optional[date] = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------

class MilestoneCreate(BaseModel):
    milestone_text: str = Field(min_length=1)
    milestone_date: Optional[date] = None
    domain: Optional[str] = None


class MilestoneOut(BaseModel):
    id: UUID
    case_id: UUID
    recorded_by: UUID
    milestone_text: str
    milestone_date: Optional[date] = None
    domain: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

class TimelineEvent(BaseModel):
    event_type: str  # note, assessment, milestone, status_change
    event_date: datetime
    title: str
    description: Optional[str] = None
    metadata: Optional[dict] = None


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class PaginatedCases(BaseModel):
    items: list[CaseListItem]
    total: int
    page: int
    page_size: int
