"""AI feature endpoints — summarisation, advice, risk detection, clinical reports."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import require_clinical
from app.auth.models import User
from app.database import get_db
from app.redis_client import redis_client

logger = logging.getLogger(__name__)

router = APIRouter()

AI_UNAVAILABLE_RESPONSE = {"result": "AI summary temporarily unavailable. Please try again later."}


# ---------------------------------------------------------------------------
# Rate limiter: 10 calls/min per user via Redis
# ---------------------------------------------------------------------------

async def check_rate_limit(user_id: UUID) -> bool:
    """Return True if the user is within the rate limit (10 calls/min)."""
    key = f"ai_rate:{user_id}"
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 60)
        return count <= 10
    except Exception:
        return True  # allow if Redis is down


# ---------------------------------------------------------------------------
# POST /summarise-notes/{case_id}
# ---------------------------------------------------------------------------

@router.post("/summarise-notes/{case_id}")
async def summarise_notes(
    case_id: UUID,
    user: User = Depends(require_clinical),
    db: AsyncSession = Depends(get_db),
):
    """Summarise all session notes for a case using AI."""
    if not await check_rate_limit(user.id):
        return {"result": "Rate limit exceeded. Please wait a minute before trying again."}

    try:
        from app.ai.summariser import summarise_case_notes

        summary = await summarise_case_notes(case_id, db)
        return {"result": summary}
    except Exception as e:
        logger.error(f"summarise_notes endpoint failed: {e}")
        return AI_UNAVAILABLE_RESPONSE


# ---------------------------------------------------------------------------
# POST /summarise-assessment/{response_id}
# ---------------------------------------------------------------------------

@router.post("/summarise-assessment/{response_id}")
async def summarise_assessment(
    response_id: UUID,
    user: User = Depends(require_clinical),
    db: AsyncSession = Depends(get_db),
):
    """Explain assessment results in parent-friendly language."""
    if not await check_rate_limit(user.id):
        return {"result": "Rate limit exceeded. Please wait a minute before trying again."}

    try:
        from app.ai.summariser import summarise_assessment_results

        summary = await summarise_assessment_results(response_id, db)
        return {"result": summary}
    except Exception as e:
        logger.error(f"summarise_assessment endpoint failed: {e}")
        return AI_UNAVAILABLE_RESPONSE


# ---------------------------------------------------------------------------
# POST /parent-advice/{case_id}
# ---------------------------------------------------------------------------

@router.post("/parent-advice/{case_id}")
async def parent_advice(
    case_id: UUID,
    user: User = Depends(require_clinical),
    db: AsyncSession = Depends(get_db),
):
    """Generate practical parent advice based on the child's case data."""
    if not await check_rate_limit(user.id):
        return {"result": "Rate limit exceeded. Please wait a minute before trying again."}

    try:
        from app.ai.advice import generate_parent_advice

        advice = await generate_parent_advice(case_id, db)
        return {"result": advice}
    except Exception as e:
        logger.error(f"parent_advice endpoint failed: {e}")
        return AI_UNAVAILABLE_RESPONSE


# ---------------------------------------------------------------------------
# POST /intervention-suggestions/{case_id}
# ---------------------------------------------------------------------------

@router.post("/intervention-suggestions/{case_id}")
async def intervention_suggestions(
    case_id: UUID,
    user: User = Depends(require_clinical),
    db: AsyncSession = Depends(get_db),
):
    """Generate evidence-based intervention suggestions."""
    if not await check_rate_limit(user.id):
        return {"result": "Rate limit exceeded. Please wait a minute before trying again."}

    try:
        from app.ai.advice import generate_intervention_suggestions

        suggestions = await generate_intervention_suggestions(case_id, db)
        return {"result": suggestions}
    except Exception as e:
        logger.error(f"intervention_suggestions endpoint failed: {e}")
        return AI_UNAVAILABLE_RESPONSE


# ---------------------------------------------------------------------------
# POST /risk-detect/{case_id}
# ---------------------------------------------------------------------------

@router.post("/risk-detect/{case_id}")
async def risk_detect(
    case_id: UUID,
    user: User = Depends(require_clinical),
    db: AsyncSession = Depends(get_db),
):
    """Detect behavioural risks from case notes and assessment trends."""
    if not await check_rate_limit(user.id):
        return {"result": "Rate limit exceeded. Please wait a minute before trying again."}

    try:
        from app.ai.risk_detector import detect_behavioural_risks

        risks = await detect_behavioural_risks(case_id, db)
        return {"result": risks}
    except Exception as e:
        logger.error(f"risk_detect endpoint failed: {e}")
        return AI_UNAVAILABLE_RESPONSE


# ---------------------------------------------------------------------------
# POST /clinical-summary/{case_id}
# ---------------------------------------------------------------------------

@router.post("/clinical-summary/{case_id}")
async def clinical_summary(
    case_id: UUID,
    user: User = Depends(require_clinical),
    db: AsyncSession = Depends(get_db),
):
    """Generate a structured clinical summary report for a case."""
    if not await check_rate_limit(user.id):
        return {"result": "Rate limit exceeded. Please wait a minute before trying again."}

    try:
        from app.ai.clinical_summary import generate_clinical_summary_report

        report = await generate_clinical_summary_report(case_id, db)
        return {"result": report}
    except Exception as e:
        logger.error(f"clinical_summary endpoint failed: {e}")
        return AI_UNAVAILABLE_RESPONSE
