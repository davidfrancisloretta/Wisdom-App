"""Assessments domain models — full DSM-5 assessment schema."""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source_pdf_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    age_range_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    age_range_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sections: Mapped[list["AssessmentSection"]] = relationship(back_populates="assessment")
    domains: Mapped[list["AssessmentDomain"]] = relationship(back_populates="assessment")


class AssessmentSection(Base):
    __tablename__ = "assessment_sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessments.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    assessment: Mapped["Assessment"] = relationship(back_populates="sections")
    questions: Mapped[list["AssessmentQuestion"]] = relationship(back_populates="section")


class AssessmentDomain(Base):
    __tablename__ = "assessment_domains"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessments.id"), nullable=False)
    domain_name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain_code: Mapped[str] = mapped_column(String(10), nullable=False)
    threshold_further_inquiry: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    threshold_type: Mapped[str] = mapped_column(String(20), default="score")  # score / yes_no
    is_safety_critical: Mapped[bool] = mapped_column(Boolean, default=False)

    assessment: Mapped["Assessment"] = relationship(back_populates="domains")
    questions: Mapped[list["AssessmentQuestion"]] = relationship(back_populates="domain")
    domain_scores: Mapped[list["DomainScore"]] = relationship(back_populates="domain")


class AssessmentQuestion(Base):
    __tablename__ = "assessment_questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_sections.id"), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False)  # likert_5 / yes_no / multiple_choice / text
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    domain_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_domains.id"), nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    is_risk_flag: Mapped[bool] = mapped_column(Boolean, default=False)

    section: Mapped["AssessmentSection"] = relationship(back_populates="questions")
    domain: Mapped[Optional["AssessmentDomain"]] = relationship(back_populates="questions")
    answer_options: Mapped[list["AnswerOption"]] = relationship(back_populates="question")
    risk_alerts: Mapped[list["RiskAlert"]] = relationship(back_populates="triggered_by_question")


class AnswerOption(Base):
    __tablename__ = "answer_options"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_questions.id"), nullable=False)
    option_text: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    question: Mapped["AssessmentQuestion"] = relationship(back_populates="answer_options")


class AssessmentAssignment(Base):
    __tablename__ = "assessment_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessments.id"), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("child_cases.id"), nullable=False)
    assigned_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    assigned_to_parent: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/in_progress/completed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    responses: Mapped[list["AssessmentResponse"]] = relationship(back_populates="assignment")


class AssessmentResponse(Base):
    __tablename__ = "assessment_responses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_assignments.id"), nullable=False)
    submitted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_partial: Mapped[bool] = mapped_column(Boolean, default=True)
    device_info: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    assignment: Mapped["AssessmentAssignment"] = relationship(back_populates="responses")
    question_responses: Mapped[list["QuestionResponse"]] = relationship(back_populates="response")
    domain_scores: Mapped[list["DomainScore"]] = relationship(back_populates="response")
    risk_alerts: Mapped[list["RiskAlert"]] = relationship(back_populates="response")


class QuestionResponse(Base):
    __tablename__ = "question_responses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    response_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_responses.id"), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_questions.id"), nullable=False)
    answer_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    answer_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_bool: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    response: Mapped["AssessmentResponse"] = relationship(back_populates="question_responses")


class DomainScore(Base):
    __tablename__ = "domain_scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    response_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_responses.id"), nullable=False)
    domain_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_domains.id"), nullable=False)
    highest_item_score: Mapped[int] = mapped_column(Integer, nullable=False)
    domain_score: Mapped[int] = mapped_column(Integer, nullable=False)
    requires_further_inquiry: Mapped[bool] = mapped_column(Boolean, default=False)
    is_safety_alert: Mapped[bool] = mapped_column(Boolean, default=False)

    response: Mapped["AssessmentResponse"] = relationship(back_populates="domain_scores")
    domain: Mapped["AssessmentDomain"] = relationship(back_populates="domain_scores")


class RiskAlert(Base):
    __tablename__ = "risk_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    response_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_responses.id"), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("child_cases.id"), nullable=False)
    triggered_by_question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_questions.id"), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(5), nullable=False)  # P0/P1/P2
    status: Mapped[str] = mapped_column(String(20), default="open")  # open/acknowledged/resolved
    notified_therapist_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    whatsapp_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    response: Mapped["AssessmentResponse"] = relationship(back_populates="risk_alerts")
    triggered_by_question: Mapped["AssessmentQuestion"] = relationship(back_populates="risk_alerts")
