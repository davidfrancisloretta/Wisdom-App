"""Assessment scoring engine — domain scoring and P0 safety alert triggering.

SAFETY P0: Domain XII (Suicidal Ideation) — any Yes answer to Q24 or Q25
must trigger an immediate P0 risk alert with WhatsApp notification.
This is non-negotiable.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.assessments.models import (
    AssessmentAssignment,
    AssessmentDomain,
    AssessmentQuestion,
    AssessmentResponse,
    DomainScore,
    QuestionResponse,
    RiskAlert,
)
from app.auth.models import User
from app.cases.models import CaseAssignment

logger = logging.getLogger(__name__)


@dataclass
class ScoringResult:
    domain_scores: list[dict]
    alerts_triggered: bool
    alert_ids: list[UUID]


async def score_assessment_response(
    response_id: UUID,
    db: AsyncSession,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ScoringResult:
    """Score a completed assessment response.

    1. Load all QuestionResponse records for this response
    2. Group by domain
    3. For each domain: find HIGHEST item score, compare against threshold
    4. Create DomainScore records
    5. Check safety-critical domains (Domain XII)
    6. Return ScoringResult
    """
    # Load the response with its assignment
    resp_result = await db.execute(
        select(AssessmentResponse).where(AssessmentResponse.id == response_id)
    )
    response = resp_result.scalar_one_or_none()
    if not response:
        raise ValueError(f"Assessment response {response_id} not found")

    # Load assignment to get case_id
    assignment_result = await db.execute(
        select(AssessmentAssignment).where(
            AssessmentAssignment.id == response.assignment_id
        )
    )
    assignment = assignment_result.scalar_one_or_none()
    if not assignment:
        raise ValueError(f"Assessment assignment not found for response {response_id}")

    case_id = assignment.case_id

    # Load all question responses
    qr_result = await db.execute(
        select(QuestionResponse).where(QuestionResponse.response_id == response_id)
    )
    question_responses = qr_result.scalars().all()

    # Load questions with domain info
    question_ids = [qr.question_id for qr in question_responses]
    if not question_ids:
        return ScoringResult(domain_scores=[], alerts_triggered=False, alert_ids=[])

    questions_result = await db.execute(
        select(AssessmentQuestion)
        .options(selectinload(AssessmentQuestion.domain))
        .where(AssessmentQuestion.id.in_(question_ids))
    )
    questions = {q.id: q for q in questions_result.scalars().all()}

    # Group responses by domain
    domain_responses: dict[UUID, list[tuple[QuestionResponse, AssessmentQuestion]]] = {}
    for qr in question_responses:
        question = questions.get(qr.question_id)
        if not question or not question.domain_id:
            continue
        if question.domain_id not in domain_responses:
            domain_responses[question.domain_id] = []
        domain_responses[question.domain_id].append((qr, question))

    # Load all domains for this assessment
    assessment_id = assignment.assessment_id
    domains_result = await db.execute(
        select(AssessmentDomain).where(AssessmentDomain.assessment_id == assessment_id)
    )
    domains = {d.id: d for d in domains_result.scalars().all()}

    # Score each domain
    scored_domains = []
    alerts_triggered = False
    alert_ids = []

    for domain_id, responses in domain_responses.items():
        domain = domains.get(domain_id)
        if not domain:
            continue

        # For yes_no domains, check if ANY answer is True/Yes
        if domain.threshold_type == "yes_no":
            highest_score = 0
            safety_triggered = False

            for qr, question in responses:
                if qr.answer_bool is True or qr.answer_value == 1:
                    highest_score = 1
                    # Check if this is a safety-critical domain
                    if domain.is_safety_critical and question.is_risk_flag:
                        safety_triggered = True
                        # TRIGGER P0 SAFETY ALERT IMMEDIATELY
                        alert_id = await trigger_p0_safety_alert(
                            response_id=response_id,
                            case_id=case_id,
                            triggered_by_question_id=question.id,
                            db=db,
                            background_tasks=background_tasks,
                        )
                        if alert_id:
                            alert_ids.append(alert_id)
                            alerts_triggered = True

            requires_further = highest_score >= (domain.threshold_further_inquiry or 1)
            domain_total = sum(
                1 for qr, _ in responses
                if qr.answer_bool is True or qr.answer_value == 1
            )
        else:
            # For score-based domains, find the HIGHEST item score (not sum)
            scores = []
            for qr, question in responses:
                score = qr.answer_value if qr.answer_value is not None else 0
                scores.append(score)

            highest_score = max(scores) if scores else 0
            domain_total = sum(scores)
            requires_further = highest_score >= (domain.threshold_further_inquiry or 2)

        # Create DomainScore record
        domain_score = DomainScore(
            response_id=response_id,
            domain_id=domain_id,
            highest_item_score=highest_score,
            domain_score=domain_total,
            requires_further_inquiry=requires_further,
            is_safety_alert=domain.is_safety_critical and requires_further,
        )
        db.add(domain_score)

        scored_domains.append({
            "domain_name": domain.domain_name,
            "domain_code": domain.domain_code,
            "highest_item_score": highest_score,
            "domain_total": domain_total,
            "threshold": domain.threshold_further_inquiry,
            "requires_further_inquiry": requires_further,
            "is_safety_critical": domain.is_safety_critical,
            "is_safety_alert": domain.is_safety_critical and requires_further,
        })

    await db.commit()

    return ScoringResult(
        domain_scores=scored_domains,
        alerts_triggered=alerts_triggered,
        alert_ids=alert_ids,
    )


async def trigger_p0_safety_alert(
    response_id: UUID,
    case_id: UUID,
    triggered_by_question_id: UUID,
    db: AsyncSession,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Optional[UUID]:
    """SAFETY P0 — THIS FUNCTION MUST NEVER FAIL SILENTLY.

    1. Create RiskAlert record with severity=P0, status=open
    2. Find the primary therapist assigned to this case
    3. Queue WhatsApp message to therapist
    4. Create in-app notification
    5. Write to AuditLog
    6. If therapist phone not found: send to all Chief Therapists
    7. If WhatsApp fails: write to DeadLetterQueue AND log error
    """
    try:
        # 1. Create RiskAlert record
        alert = RiskAlert(
            response_id=response_id,
            case_id=case_id,
            triggered_by_question_id=triggered_by_question_id,
            alert_type="suicidal_ideation",
            severity="P0",
            status="open",
        )
        db.add(alert)
        await db.flush()  # Get the alert ID without committing

        # 2. Find primary therapist
        therapist_result = await db.execute(
            select(CaseAssignment).where(
                CaseAssignment.case_id == case_id,
                CaseAssignment.assignment_type == "primary_therapist",
                CaseAssignment.is_active == True,  # noqa: E712
            )
        )
        therapist_assignment = therapist_result.scalar_one_or_none()

        therapist = None
        if therapist_assignment:
            user_result = await db.execute(
                select(User).where(User.id == therapist_assignment.user_id)
            )
            therapist = user_result.scalar_one_or_none()

        # Get case number for notification
        from app.cases.models import ChildCase
        case_result = await db.execute(
            select(ChildCase).where(ChildCase.id == case_id)
        )
        case = case_result.scalar_one_or_none()
        case_number = case.case_number if case else "UNKNOWN"

        # Get question text
        question_result = await db.execute(
            select(AssessmentQuestion).where(
                AssessmentQuestion.id == triggered_by_question_id
            )
        )
        question = question_result.scalar_one_or_none()
        question_text = question.question_text if question else "Unknown question"

        # Get assessment name
        assessment_name = "DSM-5 Cross-Cutting Symptom Measure"
        if question and question.section_id:
            from app.assessments.models import AssessmentSection, Assessment
            section_result = await db.execute(
                select(AssessmentSection).where(
                    AssessmentSection.id == question.section_id
                )
            )
            section = section_result.scalar_one_or_none()
            if section:
                assessment_result = await db.execute(
                    select(Assessment).where(Assessment.id == section.assessment_id)
                )
                assessment = assessment_result.scalar_one_or_none()
                if assessment:
                    assessment_name = assessment.title

        # 3. Queue WhatsApp message
        recipients = []
        if therapist and therapist.phone:
            recipients.append(therapist)
            alert.notified_therapist_id = therapist.id
        else:
            # 6. Fallback: send to all Chief Therapists
            from app.auth.models import Role
            chief_role_result = await db.execute(
                select(Role).where(Role.name == "chief_therapist")
            )
            chief_role = chief_role_result.scalar_one_or_none()
            if chief_role:
                chiefs_result = await db.execute(
                    select(User).where(
                        User.role_id == chief_role.id,
                        User.is_active == True,  # noqa: E712
                    )
                )
                recipients = list(chiefs_result.scalars().all())

        for recipient in recipients:
            if recipient.phone:
                try:
                    from app.messaging.whatsapp import send_whatsapp_template
                    await send_whatsapp_template(
                        recipient_phone=recipient.phone,
                        template_name="RISK_ALERT_P0",
                        template_params={
                            "case_number": case_number,
                            "assessment_name": assessment_name,
                            "question_text": question_text,
                            "submitted_at": datetime.now(timezone.utc).isoformat(),
                        },
                        case_id=case_id,
                        risk_alert_id=alert.id,
                        db=db,
                    )
                    alert.whatsapp_sent = True
                    alert.whatsapp_sent_at = datetime.now(timezone.utc)
                except Exception as e:
                    # 7. WhatsApp failure — write to DeadLetterQueue
                    logger.error(f"P0 ALERT: WhatsApp send failed: {e}")
                    from app.messaging.dead_letter import enqueue_dead_letter
                    await enqueue_dead_letter(
                        service="whatsapp",
                        payload={
                            "alert_id": str(alert.id),
                            "recipient_phone": recipient.phone,
                            "case_number": case_number,
                            "template_name": "RISK_ALERT_P0",
                        },
                        error_message=f"P0 SAFETY ALERT WhatsApp failed: {str(e)}",
                        db=db,
                    )

        # 4. Create in-app notification for all recipients
        from app.messaging.notifications import create_notification
        for recipient in recipients:
            await create_notification(
                user_id=recipient.id,
                title="⚠️ P0 SAFETY ALERT — Suicidal Ideation Detected",
                body=f"Case {case_number}: Domain XII triggered. "
                     f"Question: '{question_text[:100]}...' "
                     f"Assessment: {assessment_name}. Immediate action required.",
                notification_type="risk_alert",
                related_resource_type="risk_alert",
                related_resource_id=alert.id,
                db=db,
            )

        # 5. Write to AuditLog
        from app.cases.models import AuditLog
        audit_entry = AuditLog(
            user_id=None,  # System-triggered
            action="P0_RISK_ALERT_TRIGGERED",
            resource_type="risk_alert",
            resource_id=str(alert.id),
            old_values=None,
            new_values={
                "case_id": str(case_id),
                "case_number": case_number,
                "severity": "P0",
                "question_id": str(triggered_by_question_id),
                "question_text": question_text,
                "whatsapp_sent": alert.whatsapp_sent,
                "notified_recipients": len(recipients),
            },
        )
        db.add(audit_entry)

        await db.commit()
        logger.critical(
            f"P0 SAFETY ALERT TRIGGERED: case={case_number}, "
            f"alert_id={alert.id}, whatsapp_sent={alert.whatsapp_sent}"
        )
        return alert.id

    except Exception as e:
        logger.critical(f"P0 SAFETY ALERT CREATION FAILED: {e}", exc_info=True)
        # Even if the main flow fails, try to at least create the audit log
        try:
            from app.cases.models import AuditLog
            audit_entry = AuditLog(
                action="P0_RISK_ALERT_FAILED",
                resource_type="risk_alert",
                resource_id=str(response_id),
                new_values={
                    "error": str(e),
                    "case_id": str(case_id),
                    "question_id": str(triggered_by_question_id),
                },
            )
            db.add(audit_entry)
            await db.commit()
        except Exception:
            logger.critical("CATASTROPHIC: Failed to even log the P0 alert failure")
        raise
