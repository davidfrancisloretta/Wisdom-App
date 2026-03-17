"""AI-powered case note and assessment summarisation."""

import json
import logging
from uuid import UUID

import litellm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.redis_client import redis_client

logger = logging.getLogger(__name__)

AI_UNAVAILABLE = "AI summary temporarily unavailable. Please try again later."


async def summarise_case_notes(case_id: UUID, db: AsyncSession) -> str:
    """
    Retrieve all session notes for a case (decrypted), send to LLM for summary.
    Cache in Redis: case:{id}:notes_summary, TTL 1 hour.
    """
    # Check cache first
    cache_key = f"case:{case_id}:notes_summary"
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return cached
    except Exception:
        pass

    # Fetch notes
    from app.cases.models import CaseNote
    from app.security.encryption import decrypt_field

    result = await db.execute(
        select(CaseNote)
        .where(CaseNote.case_id == case_id)
        .order_by(CaseNote.session_date.asc())
    )
    notes = result.scalars().all()

    if not notes:
        return "No session notes found for this case."

    # Build notes text (decrypt content)
    notes_text = ""
    for note in notes:
        try:
            content = decrypt_field(note.content) if note.content else "(no content)"
        except Exception:
            content = note.content or "(no content)"
        date_str = (
            note.session_date.strftime("%Y-%m-%d") if note.session_date else "Unknown date"
        )
        notes_text += f"\n--- {note.note_type} -- {date_str} ---\n{content}\n"

    system_prompt = (
        "You are a clinical assistant helping therapists at a trauma recovery centre "
        "for children and teenagers in India. Summarise the following therapy session "
        "notes into a concise clinical summary. Highlight: key themes, observable "
        "progress, areas of concern, recurring patterns. Use professional clinical "
        "language. Do not invent information not present in the notes. "
        "Output maximum 300 words."
    )

    try:
        settings = get_settings()
        response = await litellm.acompletion(
            model=settings.LITELLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": notes_text},
            ],
            api_key=settings.LITELLM_API_KEY,
            max_tokens=500,
            temperature=0.3,
        )
        summary = response.choices[0].message.content.strip()

        # Cache result
        try:
            await redis_client.set(cache_key, summary, ex=3600)  # 1 hour
        except Exception:
            pass

        return summary
    except Exception as e:
        logger.error(f"LiteLLM summarise_case_notes failed: {e}")
        return AI_UNAVAILABLE


async def summarise_assessment_results(response_id: UUID, db: AsyncSession) -> str:
    """Explain assessment results in parent-friendly language."""
    from app.assessments.models import AssessmentDomain, DomainScore

    # Fetch domain scores with their domain info
    result = await db.execute(
        select(DomainScore, AssessmentDomain)
        .join(AssessmentDomain, DomainScore.domain_id == AssessmentDomain.id)
        .where(DomainScore.response_id == response_id)
    )
    rows = result.all()

    if not rows:
        return "No assessment scores found."

    # Build scores text
    scores_text = ""
    for score, domain in rows:
        flag = " [EXCEEDS THRESHOLD]" if score.requires_further_inquiry else ""
        scores_text += (
            f"- {domain.domain_name}: Score {score.domain_score} "
            f"(threshold: {domain.threshold_further_inquiry}){flag}\n"
        )

    system_prompt = (
        "You are a clinical assistant. The following are domain scores from a DSM-5 "
        "Level 1 Cross-Cutting Symptom assessment completed by a child aged 11-17. "
        "Explain these results in clear, non-jargon language that a therapist can share "
        "with a parent. Highlight domains that exceeded the threshold for further inquiry. "
        "Do not make a diagnosis. Use compassionate, hopeful language. "
        "Output maximum 200 words."
    )

    try:
        settings = get_settings()
        response = await litellm.acompletion(
            model=settings.LITELLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": scores_text},
            ],
            api_key=settings.LITELLM_API_KEY,
            max_tokens=350,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"LiteLLM summarise_assessment failed: {e}")
        return AI_UNAVAILABLE
