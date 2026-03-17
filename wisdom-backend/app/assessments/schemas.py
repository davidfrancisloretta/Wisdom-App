"""Pydantic schemas for assessment management."""

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Assessment Library
# ---------------------------------------------------------------------------

class AssessmentOut(BaseModel):
    id: UUID
    title: str
    description: Optional[str] = None
    version: Optional[str] = None
    source_pdf_filename: Optional[str] = None
    is_active: bool
    age_range_min: Optional[int] = None
    age_range_max: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AssessmentDetailOut(AssessmentOut):
    sections: list["SectionOut"] = []
    domains: list["DomainOut"] = []


# ---------------------------------------------------------------------------
# Sections & Questions
# ---------------------------------------------------------------------------

class AnswerOptionOut(BaseModel):
    id: UUID
    option_text: str
    value: int
    order_index: int

    model_config = {"from_attributes": True}


class QuestionOut(BaseModel):
    id: UUID
    question_text: str
    question_type: str
    order_index: int
    domain_id: Optional[UUID] = None
    is_required: bool
    is_risk_flag: bool
    answer_options: list[AnswerOptionOut] = []

    model_config = {"from_attributes": True}


class SectionOut(BaseModel):
    id: UUID
    title: str
    description: Optional[str] = None
    order_index: int
    questions: list[QuestionOut] = []

    model_config = {"from_attributes": True}


class DomainOut(BaseModel):
    id: UUID
    domain_name: str
    domain_code: str
    threshold_further_inquiry: Optional[int] = None
    threshold_type: str
    is_safety_critical: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Assessment Assignment
# ---------------------------------------------------------------------------

class AssignmentCreateRequest(BaseModel):
    case_id: UUID
    due_date: Optional[date] = None
    assigned_to_parent: bool = False


class AssignmentOut(BaseModel):
    id: UUID
    assessment_id: UUID
    case_id: UUID
    assigned_by: UUID
    due_date: Optional[date] = None
    assigned_to_parent: bool
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Parent Assessment Flow
# ---------------------------------------------------------------------------

class ParentAssessmentListItem(BaseModel):
    assignment_id: UUID
    assessment_id: UUID
    assessment_title: str
    status: str
    due_date: Optional[date] = None
    questions_answered: int = 0
    total_questions: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class QuestionResponseInput(BaseModel):
    question_id: UUID
    answer_value: Optional[int] = None
    answer_bool: Optional[bool] = None
    answer_text: Optional[str] = None


class SaveProgressRequest(BaseModel):
    responses: list[QuestionResponseInput]


class SubmitResponse(BaseModel):
    scores: list[dict]
    alerts_triggered: bool


# ---------------------------------------------------------------------------
# Domain Scores
# ---------------------------------------------------------------------------

class DomainScoreOut(BaseModel):
    domain_name: str
    domain_code: str
    highest_item_score: int
    threshold: Optional[int] = None
    requires_further_inquiry: bool
    is_safety_alert: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Risk Alerts
# ---------------------------------------------------------------------------

class RiskAlertOut(BaseModel):
    id: UUID
    response_id: UUID
    case_id: UUID
    alert_type: str
    severity: str
    status: str
    whatsapp_sent: bool
    created_at: datetime
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
