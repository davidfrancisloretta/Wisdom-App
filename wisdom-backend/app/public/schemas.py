"""Pydantic schemas for the Public Access Platform."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Public Content ───────────────────────────────────────────────────────────

class PublicContentOut(BaseModel):
    id: UUID
    content_type: str
    title: str
    slug: str
    body: Optional[str] = None
    tags: Optional[list | dict] = None
    is_published: bool
    author_id: Optional[UUID] = None
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ArticleListItem(BaseModel):
    id: UUID
    title: str
    slug: str
    tags: Optional[list | dict] = None
    published_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ResourceOut(BaseModel):
    id: UUID
    title: str
    slug: str
    body: Optional[str] = None
    tags: Optional[list | dict] = None
    published_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Workshops ────────────────────────────────────────────────────────────────

class WorkshopOut(BaseModel):
    id: UUID
    title: str
    description: Optional[str] = None
    facilitator_name: Optional[str] = None
    start_datetime: datetime
    end_datetime: datetime
    location: Optional[str] = None
    meeting_link: Optional[str] = None
    capacity: int = 0
    registered_count: int = 0
    is_public: bool = True
    registration_deadline: Optional[datetime] = None
    price: float = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkshopRegistrationCreate(BaseModel):
    registrant_name: str = Field(..., min_length=1, max_length=255)
    registrant_email: Optional[str] = Field(None, max_length=255)
    registrant_phone: Optional[str] = Field(None, max_length=20)


class WorkshopRegistrationOut(BaseModel):
    id: UUID
    workshop_id: UUID
    registrant_name: str
    registrant_email: Optional[str] = None
    registrant_phone: Optional[str] = None
    registered_at: datetime
    attended: bool = False

    model_config = {"from_attributes": True}


# ── Counselors ───────────────────────────────────────────────────────────────

class CounselorProfileOut(BaseModel):
    id: UUID
    display_name: str
    specializations: Optional[list | dict] = None
    languages: Optional[list | dict] = None
    bio: Optional[str] = None
    is_accepting_referrals: bool = True

    model_config = {"from_attributes": True}


class CounselorMatchQuery(BaseModel):
    issues: Optional[list[str]] = None
    language: Optional[str] = None


# ── Wellness Check ───────────────────────────────────────────────────────────

class WellnessCheckSubmission(BaseModel):
    answers: dict[str, int] = Field(
        ...,
        description="Mapping of question_id to answer value (1-5 scale)",
    )


class WellnessCheckResult(BaseModel):
    overall_score: float
    category: str  # e.g. "good", "moderate", "needs_attention"
    tips: list[str]
    crisis_helplines: list[dict]
