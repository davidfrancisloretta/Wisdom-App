"""AI-powered behavioural risk detection from case notes and assessments."""

import json
import logging
from uuid import UUID

import litellm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger(__name__)

RISK_ERROR_FALLBACK = [
    {
        "type": "error",
        "severity": "low",
        "evidence": "AI risk detection temporarily unavailable",
        "recommendation": "Please review case notes manually.",
    }
]


async def detect_behavioural_risks(case_id: UUID, db: AsyncSession) -> list[dict]:
    """
    Analyse recent case notes (decrypted) and assessment trends to detect
    behavioural risks. Returns a list of risk objects.
    """
    from app.assessments.models import (
        AssessmentAssignment,
        AssessmentDomain,
        AssessmentResponse,
        DomainScore,
    )
    from app.cases.models import CaseNote, ChildCase
    from app.security.encryption import decrypt_field

    # Get case info
    case_result = await db.execute(
        select(ChildCase).where(ChildCase.id == case_id)
    )
    case = case_result.scalar_one_or_none()
    if not case:
        return [
            {
                "type": "error",
                "severity": "low",
                "evidence": "Case not found.",
                "recommendation": "Verify the case ID is correct.",
            }
        ]

    # Get recent notes (last 10, decrypted)
    notes_result = await db.execute(
        select(CaseNote)
        .where(CaseNote.case_id == case_id)
        .order_by(CaseNote.session_date.desc().nulls_last())
        .limit(10)
    )
    notes = notes_result.scalars().all()

    context = "RECENT SESSION NOTES:\n"
    if notes:
        for note in reversed(notes):  # chronological order
            try:
                content = decrypt_field(note.content) if note.content else "(no content)"
            except Exception:
                content = note.content or "(no content)"
            date_str = (
                note.session_date.strftime("%Y-%m-%d")
                if note.session_date
                else "Unknown date"
            )
            context += f"\n--- {note.note_type} -- {date_str} ---\n{content}\n"
    else:
        context += "(no session notes available)\n"

    # Get assessment trends (last 3 responses for this case)
    responses_result = await db.execute(
        select(AssessmentResponse)
        .join(
            AssessmentAssignment,
            AssessmentResponse.assignment_id == AssessmentAssignment.id,
        )
        .where(AssessmentAssignment.case_id == case_id)
        .order_by(AssessmentResponse.completed_at.desc().nulls_last())
        .limit(3)
    )
    responses = responses_result.scalars().all()

    if responses:
        context += "\nASSESSMENT TRENDS (most recent first):\n"
        for resp in responses:
            scores_result = await db.execute(
                select(DomainScore, AssessmentDomain)
                .join(AssessmentDomain, DomainScore.domain_id == AssessmentDomain.id)
                .where(DomainScore.response_id == resp.id)
            )
            rows = scores_result.all()
            completed = (
                resp.completed_at.strftime("%Y-%m-%d") if resp.completed_at else "n/a"
            )
            context += f"\nAssessment completed: {completed}\n"
            for score, domain in rows:
                flag = " [EXCEEDS THRESHOLD]" if score.requires_further_inquiry else ""
                safety = " [SAFETY ALERT]" if score.is_safety_alert else ""
                context += (
                    f"  - {domain.domain_name}: {score.domain_score}"
                    f"{flag}{safety}\n"
                )

    context += f"\nChild age at intake: {case.age_at_intake or 'unknown'}"
    context += f"\nGender: {case.gender or 'unknown'}"
    context += f"\nCase status: {case.status}"

    system_prompt = (
        "You are a clinical risk assessment assistant at a child trauma recovery centre. "
        "Analyse the following case notes and assessment trends. Identify any behavioural "
        "risks or warning signs. Return your analysis as a JSON array where each element "
        "has these fields:\n"
        '  - "type": short label for the risk (e.g. "self-harm ideation", "withdrawal", '
        '"aggression escalation", "dissociation")\n'
        '  - "severity": one of "high", "medium", "low"\n'
        '  - "evidence": a brief sentence citing the specific note or score that raised '
        "the concern\n"
        '  - "recommendation": a brief clinical recommendation\n\n'
        "If no risks are detected, return an empty array: []\n"
        "Return ONLY valid JSON, no markdown, no explanation outside the JSON."
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
            max_tokens=600,
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            # Remove first line (```json or ```) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw = "\n".join(lines)

        risks = json.loads(raw)
        if not isinstance(risks, list):
            risks = [risks]

        # Validate structure
        validated = []
        for risk in risks:
            validated.append(
                {
                    "type": str(risk.get("type", "unknown")),
                    "severity": str(risk.get("severity", "low")),
                    "evidence": str(risk.get("evidence", "")),
                    "recommendation": str(risk.get("recommendation", "")),
                }
            )
        return validated

    except json.JSONDecodeError as e:
        logger.error(f"LiteLLM risk detection returned invalid JSON: {e}")
        return RISK_ERROR_FALLBACK
    except Exception as e:
        logger.error(f"LiteLLM detect_behavioural_risks failed: {e}")
        return RISK_ERROR_FALLBACK
