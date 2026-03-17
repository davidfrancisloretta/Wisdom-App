"""Public domain models — PublicContent, Workshops, Registrations, CounselorProfiles."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PublicContent(Base):
    __tablename__ = "public_content"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)  # article/resource/workshop/forum_post/crisis_info
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Workshop(Base):
    __tablename__ = "workshops"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    facilitator_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # online/in_person/hybrid
    meeting_link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, default=0)
    registered_count: Mapped[int] = mapped_column(Integer, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    registration_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    registrations: Mapped[list["WorkshopRegistration"]] = relationship(back_populates="workshop")


class WorkshopRegistration(Base):
    __tablename__ = "workshop_registrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workshop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workshops.id"), nullable=False)
    registrant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    registrant_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    registrant_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attended: Mapped[bool] = mapped_column(Boolean, default=False)

    workshop: Mapped["Workshop"] = relationship(back_populates="registrations")


class CounselorProfile(Base):
    __tablename__ = "counselor_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    specializations: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    languages: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_accepting_referrals: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
