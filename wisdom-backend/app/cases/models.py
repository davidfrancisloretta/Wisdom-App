"""Cases domain models — ChildCase, Assignments, Notes, Interventions, Audit."""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ChildCase(Base):
    __tablename__ = "child_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    # Encrypted PII fields
    first_name: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted
    last_name: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted
    date_of_birth: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    age_at_intake: Mapped[Optional[int]] = mapped_column(nullable=True)
    guardian_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted
    guardian_phone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted
    guardian_email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted
    guardian_relationship: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted
    school_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    referral_source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    presenting_issues: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    initial_diagnosis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/closed/on_hold
    intake_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    closed_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    assignments: Mapped[list["CaseAssignment"]] = relationship(back_populates="case")
    notes: Mapped[list["CaseNote"]] = relationship(back_populates="case")
    intervention_plans: Mapped[list["InterventionPlan"]] = relationship(back_populates="case")
    milestones: Mapped[list["ProgressMilestone"]] = relationship(back_populates="case")


class CaseAssignment(Base):
    __tablename__ = "case_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("child_cases.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    assignment_type: Mapped[str] = mapped_column(String(50), nullable=False)  # primary_therapist/nurturer/supervisor
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    assigned_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    case: Mapped["ChildCase"] = relationship(back_populates="assignments")


class CaseNote(Base):
    __tablename__ = "case_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("child_cases.id"), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    note_type: Mapped[str] = mapped_column(String(50), nullable=False)  # session/observation/intervention/progress/followup
    content: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted
    session_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    case: Mapped["ChildCase"] = relationship(back_populates="notes")


class InterventionPlan(Base):
    __tablename__ = "intervention_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("child_cases.id"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    goals: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    strategies: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    review_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    case: Mapped["ChildCase"] = relationship(back_populates="intervention_plans")


class ProgressMilestone(Base):
    __tablename__ = "progress_milestones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("child_cases.id"), nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    milestone_text: Mapped[str] = mapped_column(Text, nullable=False)
    milestone_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    domain: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    case: Mapped["ChildCase"] = relationship(back_populates="milestones")


class AuditLog(Base):
    """APPEND-ONLY — no update or delete ever permitted."""
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    old_values: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_values: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
