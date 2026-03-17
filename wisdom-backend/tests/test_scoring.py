"""Tests for the DSM-5 scoring engine and P0 safety alert system."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assessments.models import (
    Assessment,
    AssessmentAssignment,
    AssessmentDomain,
    AssessmentQuestion,
    AssessmentResponse,
    AssessmentSection,
    AnswerOption,
    DomainScore,
    QuestionResponse,
    RiskAlert,
)
from app.assessments.scoring import score_assessment_response
from app.cases.models import CaseAssignment, ChildCase
from app.auth.models import User, Role
from app.messaging.models import DeadLetterQueue, WhatsAppMessage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def dsm5_assessment(db: AsyncSession):
    """Create a minimal DSM-5 assessment with key domains for testing."""
    assessment_id = uuid.uuid4()
    assessment = Assessment(
        id=assessment_id,
        title="DSM-5-TR Test",
        version="TEST",
        is_active=True,
        age_range_min=11,
        age_range_max=17,
    )
    db.add(assessment)

    # Create section
    section_id = uuid.uuid4()
    section = AssessmentSection(
        id=section_id,
        assessment_id=assessment_id,
        title="Test Section",
        order_index=0,
    )
    db.add(section)

    # Create domains with thresholds matching real DSM-5
    domains = {
        "I": ("Somatic Symptoms", 2, "score", False),
        "II": ("Sleep Problems", 2, "score", False),
        "III": ("Inattention", 1, "score", False),
        "IV": ("Depression", 2, "score", False),
        "V": ("Anger", 2, "score", False),
        "VI": ("Irritability", 2, "score", False),
        "VII": ("Mania", 2, "score", False),
        "VIII": ("Anxiety", 2, "score", False),
        "IX": ("Psychosis", 1, "score", False),
        "X": ("Repetitive Thoughts & Behaviors", 2, "score", False),
        "XI": ("Substance Use", 1, "yes_no", False),
        "XII": ("Suicidal Ideation", 1, "yes_no", True),
    }

    domain_map = {}
    for code, (name, threshold, t_type, safety) in domains.items():
        d_id = uuid.uuid4()
        domain_map[code] = d_id
        db.add(AssessmentDomain(
            id=d_id,
            assessment_id=assessment_id,
            domain_name=name,
            domain_code=code,
            threshold_further_inquiry=threshold,
            threshold_type=t_type,
            is_safety_critical=safety,
        ))

    # Create questions (simplified set matching the 25-question structure)
    question_map = {}
    questions_data = [
        (1, "I", "likert_5", False), (2, "I", "likert_5", False),
        (3, "II", "likert_5", False), (4, "III", "likert_5", False),
        (5, "IV", "likert_5", False), (6, "IV", "likert_5", False),
        (7, "V", "likert_5", False), (8, "VI", "likert_5", False),
        (9, "VII", "likert_5", False), (10, "VII", "likert_5", False),
        (11, "VIII", "likert_5", False), (12, "VIII", "likert_5", False),
        (13, "VIII", "likert_5", False), (14, "IX", "likert_5", False),
        (15, "IX", "likert_5", False), (16, "X", "likert_5", False),
        (17, "X", "likert_5", False), (18, "X", "likert_5", False),
        (19, "X", "likert_5", False), (20, "XI", "yes_no", False),
        (21, "XI", "yes_no", False), (22, "XI", "yes_no", False),
        (23, "XI", "yes_no", False), (24, "XII", "yes_no", True),
        (25, "XII", "yes_no", True),
    ]

    for order, domain_code, q_type, risk_flag in questions_data:
        q_id = uuid.uuid4()
        question_map[order] = q_id
        db.add(AssessmentQuestion(
            id=q_id,
            section_id=section_id,
            question_text=f"Test question {order}",
            question_type=q_type,
            order_index=order,
            domain_id=domain_map[domain_code],
            is_required=True,
            is_risk_flag=risk_flag,
        ))

    await db.commit()

    return {
        "assessment_id": assessment_id,
        "section_id": section_id,
        "domain_map": domain_map,
        "question_map": question_map,
    }


@pytest_asyncio.fixture
async def test_case_with_therapist(db: AsyncSession, users: dict, child_case: ChildCase):
    """Ensure a therapist with a phone is assigned to the test case."""
    # Add phone to therapist
    therapist = users["therapist"]
    therapist.phone = "+1234567890"
    await db.commit()
    return child_case


async def _create_response_and_score(
    db: AsyncSession,
    dsm5: dict,
    case: ChildCase,
    user: User,
    answers: dict[int, int | bool],
) -> tuple:
    """Helper: create an assignment, response, question responses, and score."""
    # Create assignment
    assignment = AssessmentAssignment(
        assessment_id=dsm5["assessment_id"],
        case_id=case.id,
        assigned_by=user.id,
        assigned_to_parent=False,
        status="completed",
    )
    db.add(assignment)
    await db.flush()

    # Create response
    response = AssessmentResponse(
        assignment_id=assignment.id,
        submitted_by=user.id,
        is_partial=False,
    )
    db.add(response)
    await db.flush()

    # Create question responses
    for q_order, answer in answers.items():
        q_id = dsm5["question_map"][q_order]
        if isinstance(answer, bool):
            qr = QuestionResponse(
                response_id=response.id,
                question_id=q_id,
                answer_value=1 if answer else 0,
                answer_bool=answer,
            )
        else:
            qr = QuestionResponse(
                response_id=response.id,
                question_id=q_id,
                answer_value=answer,
            )
        db.add(qr)

    await db.commit()

    # Score
    result = await score_assessment_response(response.id, db)
    return response, result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_zeros_no_flags(
    db: AsyncSession, dsm5_assessment, users, child_case
):
    """Submit all 0s — no domains should flag."""
    answers = {i: 0 for i in range(1, 20)}
    answers.update({i: False for i in range(20, 26)})

    _, result = await _create_response_and_score(
        db, dsm5_assessment, child_case, users["therapist"], answers
    )

    for ds in result.domain_scores:
        assert ds["requires_further_inquiry"] is False, (
            f"Domain {ds['domain_code']} should not flag with all zeros"
        )
    assert result.alerts_triggered is False


@pytest.mark.asyncio
async def test_domain_iii_inattention_q4_slight(
    db: AsyncSession, dsm5_assessment, users, child_case
):
    """Domain III (Inattention) Q4 = 1 (Slight) → requires_further_inquiry=True (threshold=1)."""
    answers = {i: 0 for i in range(1, 20)}
    answers.update({i: False for i in range(20, 26)})
    answers[4] = 1  # Slight

    _, result = await _create_response_and_score(
        db, dsm5_assessment, child_case, users["therapist"], answers
    )

    domain_iii = next(ds for ds in result.domain_scores if ds["domain_code"] == "III")
    assert domain_iii["requires_further_inquiry"] is True
    assert domain_iii["highest_item_score"] == 1


@pytest.mark.asyncio
async def test_domain_ix_psychosis_q14_slight(
    db: AsyncSession, dsm5_assessment, users, child_case
):
    """Domain IX (Psychosis) Q14 = 1 → requires_further_inquiry=True (threshold=1)."""
    answers = {i: 0 for i in range(1, 20)}
    answers.update({i: False for i in range(20, 26)})
    answers[14] = 1

    _, result = await _create_response_and_score(
        db, dsm5_assessment, child_case, users["therapist"], answers
    )

    domain_ix = next(ds for ds in result.domain_scores if ds["domain_code"] == "IX")
    assert domain_ix["requires_further_inquiry"] is True


@pytest.mark.asyncio
async def test_domain_viii_anxiety_q11_slight_no_flag(
    db: AsyncSession, dsm5_assessment, users, child_case
):
    """Domain VIII (Anxiety) Q11 = 1 → requires_further_inquiry=False (threshold=2)."""
    answers = {i: 0 for i in range(1, 20)}
    answers.update({i: False for i in range(20, 26)})
    answers[11] = 1

    _, result = await _create_response_and_score(
        db, dsm5_assessment, child_case, users["therapist"], answers
    )

    domain_viii = next(ds for ds in result.domain_scores if ds["domain_code"] == "VIII")
    assert domain_viii["requires_further_inquiry"] is False
    assert domain_viii["highest_item_score"] == 1


@pytest.mark.asyncio
async def test_domain_viii_anxiety_q12_mild_flags(
    db: AsyncSession, dsm5_assessment, users, child_case
):
    """Domain VIII (Anxiety) Q12 = 2 (Mild) → requires_further_inquiry=True (threshold=2)."""
    answers = {i: 0 for i in range(1, 20)}
    answers.update({i: False for i in range(20, 26)})
    answers[12] = 2

    _, result = await _create_response_and_score(
        db, dsm5_assessment, child_case, users["therapist"], answers
    )

    domain_viii = next(ds for ds in result.domain_scores if ds["domain_code"] == "VIII")
    assert domain_viii["requires_further_inquiry"] is True
    assert domain_viii["highest_item_score"] == 2


@pytest.mark.asyncio
async def test_domain_xii_q24_yes_triggers_p0(
    db: AsyncSession, dsm5_assessment, users, test_case_with_therapist, case_assignment
):
    """Domain XII Q24 = Yes → P0 RiskAlert created, WhatsApp queued."""
    answers = {i: 0 for i in range(1, 20)}
    answers.update({i: False for i in range(20, 26)})
    answers[24] = True  # Yes to suicidal ideation

    response, result = await _create_response_and_score(
        db, dsm5_assessment, test_case_with_therapist, users["therapist"], answers
    )

    assert result.alerts_triggered is True
    assert len(result.alert_ids) >= 1

    # Verify RiskAlert record exists
    alert_result = await db.execute(
        select(RiskAlert).where(RiskAlert.response_id == response.id)
    )
    alert = alert_result.scalar_one_or_none()
    assert alert is not None
    assert alert.severity == "P0"
    assert alert.status == "open"

    # Verify WhatsApp message was queued (or in dead letter)
    wa_result = await db.execute(
        select(WhatsAppMessage).where(WhatsAppMessage.risk_alert_id == alert.id)
    )
    dl_result = await db.execute(
        select(DeadLetterQueue).where(
            DeadLetterQueue.service == "whatsapp",
        )
    )
    wa_msg = wa_result.scalar_one_or_none()
    dl_msg = dl_result.scalars().first()
    assert wa_msg is not None or dl_msg is not None, (
        "WhatsApp message must be queued or in dead letter queue"
    )


@pytest.mark.asyncio
async def test_domain_xii_q25_yes_triggers_p0(
    db: AsyncSession, dsm5_assessment, users, test_case_with_therapist, case_assignment
):
    """Domain XII Q25 = Yes → P0 RiskAlert created."""
    answers = {i: 0 for i in range(1, 20)}
    answers.update({i: False for i in range(20, 26)})
    answers[25] = True

    response, result = await _create_response_and_score(
        db, dsm5_assessment, test_case_with_therapist, users["therapist"], answers
    )

    assert result.alerts_triggered is True

    alert_result = await db.execute(
        select(RiskAlert).where(
            RiskAlert.response_id == response.id,
            RiskAlert.severity == "P0",
        )
    )
    alert = alert_result.scalar_one_or_none()
    assert alert is not None


@pytest.mark.asyncio
async def test_domain_xii_no_no_no_alert(
    db: AsyncSession, dsm5_assessment, users, child_case
):
    """Domain XII Q24=No, Q25=No → No alert created."""
    answers = {i: 0 for i in range(1, 20)}
    answers.update({i: False for i in range(20, 26)})

    response, result = await _create_response_and_score(
        db, dsm5_assessment, child_case, users["therapist"], answers
    )

    assert result.alerts_triggered is False

    alert_result = await db.execute(
        select(RiskAlert).where(RiskAlert.response_id == response.id)
    )
    assert alert_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_partial_save_resume_submit(
    db: AsyncSession, dsm5_assessment, users, child_case
):
    """Partial save + resume + submit → scores correctly from saved responses."""
    from app.assessments.service import save_progress, submit_assessment

    # Create assignment
    assignment = AssessmentAssignment(
        assessment_id=dsm5_assessment["assessment_id"],
        case_id=child_case.id,
        assigned_by=users["therapist"].id,
        assigned_to_parent=False,
        status="pending",
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)

    # Save partial progress (first 10 questions)
    partial_responses = []
    for i in range(1, 11):
        partial_responses.append({
            "question_id": dsm5_assessment["question_map"][i],
            "answer_value": 0,
        })

    await save_progress(assignment.id, users["therapist"].id, partial_responses, db)

    # Resume and add remaining questions
    remaining_responses = []
    for i in range(11, 20):
        remaining_responses.append({
            "question_id": dsm5_assessment["question_map"][i],
            "answer_value": 0,
        })
    for i in range(20, 26):
        remaining_responses.append({
            "question_id": dsm5_assessment["question_map"][i],
            "answer_value": 0,
            "answer_bool": False,
        })

    await save_progress(assignment.id, users["therapist"].id, remaining_responses, db)

    # Submit
    result = await submit_assessment(assignment.id, users["therapist"].id, db)

    assert "scores" in result
    for score in result["scores"]:
        assert score["requires_further_inquiry"] is False


@pytest.mark.asyncio
async def test_multiple_submissions_trend_data(
    db: AsyncSession, dsm5_assessment, users, child_case
):
    """Multiple submissions for same case → each creates separate domain scores."""
    # First submission with low scores
    answers_1 = {i: 0 for i in range(1, 20)}
    answers_1.update({i: False for i in range(20, 26)})
    resp_1, result_1 = await _create_response_and_score(
        db, dsm5_assessment, child_case, users["therapist"], answers_1
    )

    # Second submission with higher score on Q4
    answers_2 = {i: 0 for i in range(1, 20)}
    answers_2.update({i: False for i in range(20, 26)})
    answers_2[4] = 3  # Moderate inattention
    resp_2, result_2 = await _create_response_and_score(
        db, dsm5_assessment, child_case, users["therapist"], answers_2
    )

    # Verify two sets of domain scores exist
    scores_1 = await db.execute(
        select(DomainScore).where(DomainScore.response_id == resp_1.id)
    )
    scores_2 = await db.execute(
        select(DomainScore).where(DomainScore.response_id == resp_2.id)
    )

    count_1 = len(list(scores_1.scalars().all()))
    count_2 = len(list(scores_2.scalars().all()))

    assert count_1 > 0
    assert count_2 > 0

    # Second submission should have flagged inattention
    domain_iii = next(ds for ds in result_2.domain_scores if ds["domain_code"] == "III")
    assert domain_iii["highest_item_score"] == 3
    assert domain_iii["requires_further_inquiry"] is True
