"""Assessment API — library, admin upload, assignment, parent flow, results, risk alerts."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import audit_service
from app.auth.abac import check_case_access
from app.auth.guards import get_current_user, require_role
from app.auth.models import User
from app.assessments import service as assessment_service
from app.assessments.schemas import (
    AssessmentOut,
    AssignmentCreateRequest,
    AssignmentOut,
    DomainScoreOut,
    ParentAssessmentListItem,
    RiskAlertOut,
    SaveProgressRequest,
    SubmitResponse,
)
from app.database import get_db

router = APIRouter()


# ---------------------------------------------------------------------------
# Assessment Library (staff)
# ---------------------------------------------------------------------------

@router.get("")
async def list_assessments(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all assessments with domain/question counts."""
    from app.assessments.models import AssessmentDomain, AssessmentSection, AssessmentQuestion
    from sqlalchemy import func

    assessments = await assessment_service.list_assessments(db)
    result = []
    for a in assessments:
        # Count domains
        domain_count = (await db.execute(
            select(func.count(AssessmentDomain.id)).where(AssessmentDomain.assessment_id == a.id)
        )).scalar() or 0

        # Count questions (across all sections)
        section_ids_result = await db.execute(
            select(AssessmentSection.id).where(AssessmentSection.assessment_id == a.id)
        )
        section_ids = [r[0] for r in section_ids_result.all()]
        question_count = 0
        if section_ids:
            question_count = (await db.execute(
                select(func.count(AssessmentQuestion.id)).where(
                    AssessmentQuestion.section_id.in_(section_ids)
                )
            )).scalar() or 0

        result.append({
            "id": a.id,
            "title": a.title,
            "description": a.description,
            "version": a.version,
            "source_pdf_filename": a.source_pdf_filename,
            "is_active": a.is_active,
            "age_range_min": a.age_range_min,
            "age_range_max": a.age_range_max,
            "created_at": a.created_at,
            "domain_count": domain_count,
            "question_count": question_count,
        })
    return result


@router.get("/{assessment_id}")
async def get_assessment(
    assessment_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    detail = await assessment_service.get_assessment_detail(assessment_id, db)
    if not detail:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return detail


# ---------------------------------------------------------------------------
# Assessment Assignment
# ---------------------------------------------------------------------------

@router.post("/{assessment_id}/assign", status_code=201, response_model=AssignmentOut)
async def assign_assessment(
    assessment_id: UUID,
    data: AssignmentCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_role(
        "super_admin", "admin", "chief_therapist",
    )),
    db: AsyncSession = Depends(get_db),
):
    assignment = await assessment_service.assign_assessment(
        assessment_id=assessment_id,
        case_id=data.case_id,
        assigned_by=user.id,
        due_date=data.due_date,
        assigned_to_parent=data.assigned_to_parent,
        db=db,
    )

    await audit_service.log_event(
        user_id=user.id,
        action="ASSIGN_ASSESSMENT",
        resource_type="assessment_assignment",
        resource_id=str(assignment.id),
        old_values=None,
        new_values={
            "assessment_id": str(assessment_id),
            "case_id": str(data.case_id),
            "assigned_to_parent": data.assigned_to_parent,
        },
        request=request,
        db=db,
    )

    # If assigned to parent, send WhatsApp notification
    if data.assigned_to_parent:
        from app.cases.models import ChildCase
        case_result = await db.execute(
            select(ChildCase).where(ChildCase.id == data.case_id)
        )
        case = case_result.scalar_one_or_none()
        if case and case.guardian_phone:
            from app.security.encryption import decrypt_field
            try:
                phone = decrypt_field(case.guardian_phone)
            except Exception:
                phone = case.guardian_phone
            from app.messaging.whatsapp import send_whatsapp_template
            background_tasks.add_task(
                send_whatsapp_template,
                recipient_phone=phone,
                template_name="ASSESSMENT_ASSIGNED",
                template_params={"case_number": case.case_number},
                case_id=data.case_id,
                db=db,
            )

    return assignment


# ---------------------------------------------------------------------------
# Case Assessment Results (staff)
# ---------------------------------------------------------------------------

@router.get("/cases/{case_id}/results")
async def get_case_results(
    case_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    has_access = await check_case_access(user.id, case_id, db)
    if not has_access:
        raise HTTPException(status_code=403, detail="You do not have access to this case")

    await audit_service.log_event(
        user_id=user.id,
        action="READ_PII",
        resource_type="assessment_results",
        resource_id=str(case_id),
        old_values=None,
        new_values=None,
        request=request,
        db=db,
    )

    return await assessment_service.get_case_assessment_results(case_id, db)


# ---------------------------------------------------------------------------
# Risk Alerts
# ---------------------------------------------------------------------------

@router.get("/risk-alerts/{case_id}", response_model=list[RiskAlertOut])
async def get_risk_alerts(
    case_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    has_access = await check_case_access(user.id, case_id, db)
    if not has_access:
        raise HTTPException(status_code=403, detail="You do not have access to this case")
    alerts = await assessment_service.get_case_risk_alerts(case_id, db)
    return alerts


@router.post("/risk-alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    alert = await assessment_service.acknowledge_risk_alert(alert_id, db)
    if not alert:
        raise HTTPException(status_code=404, detail="Risk alert not found")

    await audit_service.log_event(
        user_id=user.id,
        action="ACKNOWLEDGE_RISK_ALERT",
        resource_type="risk_alert",
        resource_id=str(alert_id),
        old_values={"status": "open"},
        new_values={"status": "acknowledged"},
        request=request,
        db=db,
    )
    return {"message": "Alert acknowledged", "status": alert.status}


# ---------------------------------------------------------------------------
# Admin: PDF Upload & Publish
# ---------------------------------------------------------------------------

@router.post("/admin/upload")
async def upload_assessment_pdf(
    file: UploadFile = File(...),
    user: User = Depends(require_role("super_admin", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a clinical assessment PDF, extract text, parse with AI, return preview."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    file_bytes = await file.read()

    from app.assessments.pdf_parser import extract_tables_from_pdf, extract_text_from_pdf
    raw_text = await extract_text_from_pdf(file_bytes)
    tables = await extract_tables_from_pdf(file_bytes)

    from app.assessments.ai_parser import calculate_confidence_score, parse_assessment_with_ai
    parsed = await parse_assessment_with_ai(raw_text, tables)
    confidence = await calculate_confidence_score(parsed, raw_text)

    return {
        "assessment_preview": parsed,
        "confidence_score": confidence,
        "source_filename": file.filename,
    }


@router.post("/admin/publish")
async def publish_assessment(
    request: Request,
    data: dict,
    user: User = Depends(require_role("super_admin", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Create all DB records from reviewed/edited parsed assessment JSON."""
    from app.assessments.ai_parser import create_assessment_from_parsed
    assessment = await create_assessment_from_parsed(
        parsed=data,
        uploaded_by=user.id,
        db=db,
        is_active=True,
    )

    await audit_service.log_event(
        user_id=user.id,
        action="PUBLISH_ASSESSMENT",
        resource_type="assessment",
        resource_id=str(assessment.id),
        old_values=None,
        new_values={"title": assessment.title},
        request=request,
        db=db,
    )

    return {"id": assessment.id, "title": assessment.title, "message": "Assessment published"}


# ---------------------------------------------------------------------------
# Parent Flow — separate sub-router
# ---------------------------------------------------------------------------

parent_router = APIRouter()


@parent_router.get("/assessments", response_model=list[ParentAssessmentListItem])
async def parent_list_assessments(
    user: User = Depends(require_role("parent")),
    db: AsyncSession = Depends(get_db),
):
    items = await assessment_service.get_parent_assessments(user.id, db)
    return items


@parent_router.get("/assessments/{assignment_id}")
async def parent_get_assessment(
    assignment_id: UUID,
    user: User = Depends(require_role("parent")),
    db: AsyncSession = Depends(get_db),
):
    detail = await assessment_service.get_parent_assessment_detail(
        assignment_id, user.id, db
    )
    if not detail:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return detail


@parent_router.post("/assessments/{assignment_id}/save-progress")
async def parent_save_progress(
    assignment_id: UUID,
    data: SaveProgressRequest,
    user: User = Depends(require_role("parent")),
    db: AsyncSession = Depends(get_db),
):
    result = await assessment_service.save_progress(
        assignment_id=assignment_id,
        parent_user_id=user.id,
        responses=[r.model_dump() for r in data.responses],
        db=db,
    )
    return result


@parent_router.post("/assessments/{assignment_id}/submit", response_model=SubmitResponse)
async def parent_submit_assessment(
    assignment_id: UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_role("parent")),
    db: AsyncSession = Depends(get_db),
):
    result = await assessment_service.submit_assessment(
        assignment_id=assignment_id,
        parent_user_id=user.id,
        db=db,
        background_tasks=background_tasks,
    )
    return result
