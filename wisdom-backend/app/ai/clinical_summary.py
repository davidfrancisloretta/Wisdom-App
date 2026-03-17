"""AI-generated structured clinical summary report using LiteLLM."""

import json
import logging
from uuid import UUID

import litellm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger(__name__)

AI_UNAVAILABLE = "AI summary temporarily unavailable. Please try again later."


async def generate_clinical_summary_report(case_id: UUID, db: AsyncSession) -> dict:
    """
    Generate a structured clinical summary report for a case.
    Gathers all case data: notes, assessments, milestones, interventions.
    Returns a dict with sections: presenting_problem, assessment_summary,
    treatment_progress, risk_indicators, next_steps.
    """
    from app.assessments.models import (
        AssessmentAssignment,
        AssessmentDomain,
        AssessmentResponse,
        DomainScore,
    )
    from app.cases.models import (
        CaseNote,
        ChildCase,
        InterventionPlan,
        ProgressMilestone,
    )
    from app.security.encryption import decrypt_field

    # Get case info
    case_result = await db.execute(
        select(ChildCase).where(ChildCase.id == case_id)
    )
    case = case_result.scalar_one_or_none()
    if not case:
        return {"error": "Case not found."}

    # Build comprehensive context
    context = "=== CASE INFORMATION ===\n"
    context += f"Case number: {case.case_number}\n"
    context += f"Age at intake: {case.age_at_intake or 'unknown'}\n"
    context += f"Gender: {case.gender or 'unknown'}\n"
    context += f"Status: {case.status}\n"
    context += f"Intake date: {case.intake_date or 'unknown'}\n"

    if case.presenting_issues:
        context += "\nPRESENTING ISSUES:\n"
        if isinstance(case.presenting_issues, list):
            context += "\n".join(f"- {issue}" for issue in case.presenting_issues)
        elif isinstance(case.presenting_issues, dict):
            for key, val in case.presenting_issues.items():
                context += f"- {key}: {val}\n"
        else:
            context += str(case.presenting_issues)
        context += "\n"

    if case.initial_diagnosis:
        context += f"\nInitial diagnosis: {case.initial_diagnosis}\n"

    # Session notes (all, decrypted)
    notes_result = await db.execute(
        select(CaseNote)
        .where(CaseNote.case_id == case_id)
        .order_by(CaseNote.session_date.asc().nulls_last())
    )
    notes = notes_result.scalars().all()

    context += "\n=== SESSION NOTES ===\n"
    if notes:
        for note in notes:
            try:
                content = decrypt_field(note.content) if note.content else "(no content)"
            except Exception:
                content = note.content or "(no content)"
            date_str = (
                note.session_date.strftime("%Y-%m-%d")
                if note.session_date
                else "Unknown date"
            )
            context += f"\n[{note.note_type.upper()} -- {date_str}]\n{content}\n"
    else:
        context += "(no session notes)\n"

    # Assessment scores (all responses for this case)
    responses_result = await db.execute(
        select(AssessmentResponse)
        .join(
            AssessmentAssignment,
            AssessmentResponse.assignment_id == AssessmentAssignment.id,
        )
        .where(AssessmentAssignment.case_id == case_id)
        .order_by(AssessmentResponse.completed_at.asc().nulls_last())
    )
    responses = responses_result.scalars().all()

    context += "\n=== ASSESSMENT RESULTS ===\n"
    if responses:
        for resp in responses:
            completed = (
                resp.completed_at.strftime("%Y-%m-%d") if resp.completed_at else "n/a"
            )
            context += f"\nAssessment completed: {completed}\n"
            scores_result = await db.execute(
                select(DomainScore, AssessmentDomain)
                .join(AssessmentDomain, DomainScore.domain_id == AssessmentDomain.id)
                .where(DomainScore.response_id == resp.id)
            )
            rows = scores_result.all()
            for score, domain in rows:
                flag = " [EXCEEDS THRESHOLD]" if score.requires_further_inquiry else ""
                safety = " [SAFETY ALERT]" if score.is_safety_alert else ""
                context += (
                    f"  - {domain.domain_name}: {score.domain_score}"
                    f" (threshold: {domain.threshold_further_inquiry}){flag}{safety}\n"
                )
    else:
        context += "(no assessments completed)\n"

    # Intervention plans
    plans_result = await db.execute(
        select(InterventionPlan)
        .where(InterventionPlan.case_id == case_id)
        .order_by(InterventionPlan.created_at.asc())
    )
    plans = plans_result.scalars().all()

    context += "\n=== INTERVENTION PLANS ===\n"
    if plans:
        for plan in plans:
            context += f"\nPlan status: {plan.status}\n"
            if plan.goals:
                context += f"Goals: {json.dumps(plan.goals, default=str)}\n"
            if plan.strategies:
                context += f"Strategies: {json.dumps(plan.strategies, default=str)}\n"
            if plan.review_date:
                context += f"Review date: {plan.review_date}\n"
    else:
        context += "(no intervention plans)\n"

    # Progress milestones
    milestones_result = await db.execute(
        select(ProgressMilestone)
        .where(ProgressMilestone.case_id == case_id)
        .order_by(ProgressMilestone.milestone_date.asc().nulls_last())
    )
    milestones = milestones_result.scalars().all()

    context += "\n=== PROGRESS MILESTONES ===\n"
    if milestones:
        for ms in milestones:
            ms_date = (
                ms.milestone_date.strftime("%Y-%m-%d") if ms.milestone_date else "n/a"
            )
            domain_str = f" ({ms.domain})" if ms.domain else ""
            context += f"- [{ms_date}]{domain_str} {ms.milestone_text}\n"
    else:
        context += "(no milestones recorded)\n"

    system_prompt = (
        "You are a senior clinical supervisor at a child trauma recovery centre in India. "
        "Generate a structured clinical summary report based on the case data below. "
        "Return your response as a JSON object with exactly these keys:\n"
        '  - "presenting_problem": summary of the presenting issues and initial '
        "diagnosis (1-3 sentences)\n"
        '  - "assessment_summary": overview of assessment results and trends '
        "(1-3 sentences)\n"
        '  - "treatment_progress": summary of treatment progress based on session '
        "notes and milestones (2-4 sentences)\n"
        '  - "risk_indicators": any identified risk indicators or safety concerns '
        "(1-3 sentences, or 'None identified' if none)\n"
        '  - "next_steps": recommended next steps for the clinical team '
        "(2-4 bullet points as a single string)\n\n"
        "Use professional clinical language. Do not invent information not present "
        "in the data. Return ONLY valid JSON, no markdown, no explanation outside the JSON."
    )

    try:
        settings = get_settings()
        response = await litellm.acompletion(
            model=settings.LITELLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            api_key=settings.LITELLM_API_KEY,
            max_tokens=800,
            temperature=0.3,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw = "\n".join(lines)

        report = json.loads(raw)

        # Ensure all expected keys are present
        expected_keys = [
            "presenting_problem",
            "assessment_summary",
            "treatment_progress",
            "risk_indicators",
            "next_steps",
        ]
        for key in expected_keys:
            if key not in report:
                report[key] = "Not available."

        return report

    except json.JSONDecodeError as e:
        logger.error(f"LiteLLM clinical summary returned invalid JSON: {e}")
        return {"error": AI_UNAVAILABLE}
    except Exception as e:
        logger.error(f"LiteLLM generate_clinical_summary_report failed: {e}")
        return {"error": AI_UNAVAILABLE}
