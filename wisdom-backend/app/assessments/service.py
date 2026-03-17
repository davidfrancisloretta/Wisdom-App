"""Assessment business logic — library, assignments, parent flow, results."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.assessments.models import (
    AnswerOption,
    Assessment,
    AssessmentAssignment,
    AssessmentDomain,
    AssessmentQuestion,
    AssessmentResponse,
    AssessmentSection,
    DomainScore,
    QuestionResponse,
    RiskAlert,
)


# ---------------------------------------------------------------------------
# Assessment Library
# ---------------------------------------------------------------------------

async def list_assessments(db: AsyncSession) -> list[Assessment]:
    result = await db.execute(
        select(Assessment).order_by(Assessment.created_at.desc())
    )
    return list(result.scalars().all())


async def get_assessment_detail(assessment_id: UUID, db: AsyncSession) -> Optional[dict]:
    """Get full assessment with sections, questions, domains, and answer options."""
    result = await db.execute(
        select(Assessment).where(Assessment.id == assessment_id)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        return None

    # Load sections with questions and answer options
    sections_result = await db.execute(
        select(AssessmentSection)
        .where(AssessmentSection.assessment_id == assessment_id)
        .order_by(AssessmentSection.order_index)
    )
    sections = sections_result.scalars().all()

    sections_out = []
    total_questions = 0
    for section in sections:
        questions_result = await db.execute(
            select(AssessmentQuestion)
            .where(AssessmentQuestion.section_id == section.id)
            .order_by(AssessmentQuestion.order_index)
        )
        questions = questions_result.scalars().all()
        total_questions += len(questions)

        questions_out = []
        for q in questions:
            options_result = await db.execute(
                select(AnswerOption)
                .where(AnswerOption.question_id == q.id)
                .order_by(AnswerOption.order_index)
            )
            options = options_result.scalars().all()

            questions_out.append({
                "id": q.id,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "order_index": q.order_index,
                "domain_id": q.domain_id,
                "is_required": q.is_required,
                "is_risk_flag": q.is_risk_flag,
                "answer_options": [
                    {
                        "id": o.id,
                        "option_text": o.option_text,
                        "value": o.value,
                        "order_index": o.order_index,
                    }
                    for o in options
                ],
            })

        sections_out.append({
            "id": section.id,
            "title": section.title,
            "description": section.description,
            "order_index": section.order_index,
            "questions": questions_out,
        })

    # Load domains
    domains_result = await db.execute(
        select(AssessmentDomain)
        .where(AssessmentDomain.assessment_id == assessment_id)
    )
    domains = domains_result.scalars().all()

    return {
        "id": assessment.id,
        "title": assessment.title,
        "description": assessment.description,
        "version": assessment.version,
        "source_pdf_filename": assessment.source_pdf_filename,
        "is_active": assessment.is_active,
        "age_range_min": assessment.age_range_min,
        "age_range_max": assessment.age_range_max,
        "created_at": assessment.created_at,
        "total_questions": total_questions,
        "sections": sections_out,
        "domains": [
            {
                "id": d.id,
                "domain_name": d.domain_name,
                "domain_code": d.domain_code,
                "threshold_further_inquiry": d.threshold_further_inquiry,
                "threshold_type": d.threshold_type,
                "is_safety_critical": d.is_safety_critical,
            }
            for d in domains
        ],
    }


# ---------------------------------------------------------------------------
# Assessment Assignment
# ---------------------------------------------------------------------------

async def assign_assessment(
    assessment_id: UUID,
    case_id: UUID,
    assigned_by: UUID,
    due_date=None,
    assigned_to_parent: bool = False,
    db: AsyncSession = None,
) -> AssessmentAssignment:
    assignment = AssessmentAssignment(
        assessment_id=assessment_id,
        case_id=case_id,
        assigned_by=assigned_by,
        due_date=due_date,
        assigned_to_parent=assigned_to_parent,
        status="pending",
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


# ---------------------------------------------------------------------------
# Parent Assessment Flow
# ---------------------------------------------------------------------------

async def get_parent_assessments(
    parent_user_id: UUID, db: AsyncSession
) -> list[dict]:
    """Get all assessments assigned to the parent's child case(s)."""
    from app.auth.models import UserAttribute

    # Get parent's linked case IDs
    attrs_result = await db.execute(
        select(UserAttribute).where(
            UserAttribute.user_id == parent_user_id,
            UserAttribute.attribute_key == "child_case_id",
        )
    )
    attrs = attrs_result.scalars().all()
    case_ids = [UUID(a.attribute_value) for a in attrs]

    if not case_ids:
        return []

    # Get assignments for these cases (assigned to parent)
    assignments_result = await db.execute(
        select(AssessmentAssignment).where(
            AssessmentAssignment.case_id.in_(case_ids),
            AssessmentAssignment.assigned_to_parent == True,  # noqa: E712
        )
    )
    assignments = assignments_result.scalars().all()

    items = []
    for a in assignments:
        # Get assessment info
        assess_result = await db.execute(
            select(Assessment).where(Assessment.id == a.assessment_id)
        )
        assessment = assess_result.scalar_one_or_none()
        if not assessment:
            continue

        # Get total questions count
        sections_result = await db.execute(
            select(AssessmentSection.id).where(
                AssessmentSection.assessment_id == a.assessment_id
            )
        )
        section_ids = [r[0] for r in sections_result.all()]
        total_q = 0
        if section_ids:
            total_result = await db.execute(
                select(func.count(AssessmentQuestion.id)).where(
                    AssessmentQuestion.section_id.in_(section_ids)
                )
            )
            total_q = total_result.scalar() or 0

        # Get answered count (from partial responses)
        answered = 0
        resp_result = await db.execute(
            select(AssessmentResponse).where(
                AssessmentResponse.assignment_id == a.id,
                AssessmentResponse.submitted_by == parent_user_id,
            ).order_by(AssessmentResponse.started_at.desc()).limit(1)
        )
        response = resp_result.scalar_one_or_none()
        if response:
            answered_result = await db.execute(
                select(func.count(QuestionResponse.id)).where(
                    QuestionResponse.response_id == response.id
                )
            )
            answered = answered_result.scalar() or 0

        items.append({
            "assignment_id": a.id,
            "assessment_id": a.assessment_id,
            "assessment_title": assessment.title,
            "status": a.status,
            "due_date": a.due_date,
            "questions_answered": answered,
            "total_questions": total_q,
            "created_at": a.created_at,
        })

    return items


async def get_parent_assessment_detail(
    assignment_id: UUID, parent_user_id: UUID, db: AsyncSession
) -> Optional[dict]:
    """Get full assessment with previous partial responses for a parent."""
    assignment_result = await db.execute(
        select(AssessmentAssignment).where(AssessmentAssignment.id == assignment_id)
    )
    assignment = assignment_result.scalar_one_or_none()
    if not assignment:
        return None

    # Get the full assessment detail
    detail = await get_assessment_detail(assignment.assessment_id, db)
    if not detail:
        return None

    # Get any existing partial response
    resp_result = await db.execute(
        select(AssessmentResponse).where(
            AssessmentResponse.assignment_id == assignment_id,
            AssessmentResponse.submitted_by == parent_user_id,
        ).order_by(AssessmentResponse.started_at.desc()).limit(1)
    )
    existing_response = resp_result.scalar_one_or_none()

    saved_responses = {}
    if existing_response:
        qr_result = await db.execute(
            select(QuestionResponse).where(
                QuestionResponse.response_id == existing_response.id
            )
        )
        for qr in qr_result.scalars().all():
            saved_responses[str(qr.question_id)] = {
                "answer_value": qr.answer_value,
                "answer_bool": qr.answer_bool,
                "answer_text": qr.answer_text,
            }

    detail["assignment_id"] = assignment_id
    detail["assignment_status"] = assignment.status
    detail["due_date"] = assignment.due_date
    detail["saved_responses"] = saved_responses
    detail["response_id"] = str(existing_response.id) if existing_response else None

    return detail


async def save_progress(
    assignment_id: UUID,
    parent_user_id: UUID,
    responses: list[dict],
    db: AsyncSession,
) -> dict:
    """Save partial responses. Creates or updates AssessmentResponse + QuestionResponses."""
    # Get or create response
    resp_result = await db.execute(
        select(AssessmentResponse).where(
            AssessmentResponse.assignment_id == assignment_id,
            AssessmentResponse.submitted_by == parent_user_id,
            AssessmentResponse.is_partial == True,  # noqa: E712
        ).order_by(AssessmentResponse.started_at.desc()).limit(1)
    )
    response = resp_result.scalar_one_or_none()

    if not response:
        response = AssessmentResponse(
            assignment_id=assignment_id,
            submitted_by=parent_user_id,
            started_at=datetime.now(timezone.utc),
            is_partial=True,
        )
        db.add(response)
        await db.flush()

    # Update assignment status
    assignment_result = await db.execute(
        select(AssessmentAssignment).where(AssessmentAssignment.id == assignment_id)
    )
    assignment = assignment_result.scalar_one_or_none()
    if assignment and assignment.status == "pending":
        assignment.status = "in_progress"

    # Upsert question responses
    for r in responses:
        question_id = r["question_id"]
        existing_result = await db.execute(
            select(QuestionResponse).where(
                QuestionResponse.response_id == response.id,
                QuestionResponse.question_id == question_id,
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            if r.get("answer_value") is not None:
                existing.answer_value = r["answer_value"]
            if r.get("answer_bool") is not None:
                existing.answer_bool = r["answer_bool"]
            if r.get("answer_text") is not None:
                existing.answer_text = r["answer_text"]
        else:
            qr = QuestionResponse(
                response_id=response.id,
                question_id=question_id,
                answer_value=r.get("answer_value"),
                answer_bool=r.get("answer_bool"),
                answer_text=r.get("answer_text"),
            )
            db.add(qr)

    await db.commit()
    await db.refresh(response)

    return {"response_id": str(response.id), "saved": len(responses)}


async def submit_assessment(
    assignment_id: UUID,
    parent_user_id: UUID,
    db: AsyncSession,
    background_tasks=None,
) -> dict:
    """Mark assessment as complete and trigger scoring."""
    # Get the partial response
    resp_result = await db.execute(
        select(AssessmentResponse).where(
            AssessmentResponse.assignment_id == assignment_id,
            AssessmentResponse.submitted_by == parent_user_id,
        ).order_by(AssessmentResponse.started_at.desc()).limit(1)
    )
    response = resp_result.scalar_one_or_none()

    if not response:
        raise ValueError("No responses found. Please answer questions before submitting.")

    # Mark as complete
    response.is_partial = False
    response.completed_at = datetime.now(timezone.utc)

    # Update assignment status
    assignment_result = await db.execute(
        select(AssessmentAssignment).where(AssessmentAssignment.id == assignment_id)
    )
    assignment = assignment_result.scalar_one_or_none()
    if assignment:
        assignment.status = "completed"

    await db.commit()

    # Score immediately
    from app.assessments.scoring import score_assessment_response
    scoring_result = await score_assessment_response(
        response.id, db, background_tasks
    )

    return {
        "scores": scoring_result.domain_scores,
        "alerts_triggered": scoring_result.alerts_triggered,
    }


# ---------------------------------------------------------------------------
# Results for Staff
# ---------------------------------------------------------------------------

async def get_case_assessment_results(
    case_id: UUID, db: AsyncSession
) -> list[dict]:
    """Get all completed assessment results for a case."""
    assignments_result = await db.execute(
        select(AssessmentAssignment).where(
            AssessmentAssignment.case_id == case_id,
        )
    )
    assignments = assignments_result.scalars().all()

    results = []
    for a in assignments:
        assess_result = await db.execute(
            select(Assessment).where(Assessment.id == a.assessment_id)
        )
        assessment = assess_result.scalar_one_or_none()

        # Get completed responses
        responses_result = await db.execute(
            select(AssessmentResponse).where(
                AssessmentResponse.assignment_id == a.id,
                AssessmentResponse.is_partial == False,  # noqa: E712
            ).order_by(AssessmentResponse.completed_at.desc())
        )
        responses = responses_result.scalars().all()

        for resp in responses:
            # Get domain scores
            scores_result = await db.execute(
                select(DomainScore)
                .options(selectinload(DomainScore.domain))
                .where(DomainScore.response_id == resp.id)
            )
            domain_scores = scores_result.scalars().all()

            results.append({
                "assignment_id": a.id,
                "assessment_id": a.assessment_id,
                "assessment_name": assessment.title if assessment else "Unknown",
                "response_id": resp.id,
                "completed_at": resp.completed_at,
                "domain_scores": [
                    {
                        "domain_name": ds.domain.domain_name if ds.domain else "Unknown",
                        "domain_code": ds.domain.domain_code if ds.domain else "?",
                        "highest_item_score": ds.highest_item_score,
                        "threshold": ds.domain.threshold_further_inquiry if ds.domain else None,
                        "requires_further_inquiry": ds.requires_further_inquiry,
                        "is_safety_alert": ds.is_safety_alert,
                    }
                    for ds in domain_scores
                ],
            })

    return results


async def get_case_risk_alerts(case_id: UUID, db: AsyncSession) -> list[RiskAlert]:
    result = await db.execute(
        select(RiskAlert)
        .where(RiskAlert.case_id == case_id)
        .order_by(RiskAlert.created_at.desc())
    )
    return list(result.scalars().all())


async def acknowledge_risk_alert(
    alert_id: UUID, db: AsyncSession
) -> Optional[RiskAlert]:
    result = await db.execute(
        select(RiskAlert).where(RiskAlert.id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if not alert:
        return None
    alert.status = "acknowledged"
    alert.acknowledged_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(alert)
    return alert
