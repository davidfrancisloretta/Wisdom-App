"""AI-powered parent advice and intervention suggestions."""

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


async def generate_parent_advice(case_id: UUID, db: AsyncSession) -> str:
    """
    Generate compassionate, practical parent advice based on the child's case data.
    Uses presenting issues and the latest domain score flags.
    """
    from app.assessments.models import (
        AssessmentAssignment,
        AssessmentDomain,
        AssessmentResponse,
        DomainScore,
    )
    from app.cases.models import ChildCase

    # Get case info
    case_result = await db.execute(
        select(ChildCase).where(ChildCase.id == case_id)
    )
    case = case_result.scalar_one_or_none()
    if not case:
        return "Case not found."

    # Build context from presenting issues
    context = "PRESENTING ISSUES:\n"
    if case.presenting_issues:
        if isinstance(case.presenting_issues, list):
            context += "\n".join(f"- {issue}" for issue in case.presenting_issues)
        elif isinstance(case.presenting_issues, dict):
            for key, val in case.presenting_issues.items():
                context += f"- {key}: {val}\n"
        else:
            context += str(case.presenting_issues)
    else:
        context += "(none recorded)"

    # Get the latest assessment domain scores for this case
    latest_response = await db.execute(
        select(AssessmentResponse)
        .join(AssessmentAssignment, AssessmentResponse.assignment_id == AssessmentAssignment.id)
        .where(AssessmentAssignment.case_id == case_id)
        .order_by(AssessmentResponse.completed_at.desc().nulls_last())
        .limit(1)
    )
    resp = latest_response.scalar_one_or_none()

    if resp:
        scores_result = await db.execute(
            select(DomainScore, AssessmentDomain)
            .join(AssessmentDomain, DomainScore.domain_id == AssessmentDomain.id)
            .where(DomainScore.response_id == resp.id)
        )
        rows = scores_result.all()
        if rows:
            context += "\n\nASSESSMENT DOMAIN FLAGS:\n"
            for score, domain in rows:
                flag = " [EXCEEDS THRESHOLD]" if score.requires_further_inquiry else ""
                context += (
                    f"- {domain.domain_name}: Score {score.domain_score}{flag}\n"
                )

    context += f"\n\nChild age at intake: {case.age_at_intake or 'unknown'}"
    context += f"\nGender: {case.gender or 'unknown'}"

    system_prompt = (
        "You are a compassionate child psychologist assistant helping parents of "
        "children in trauma recovery. Based on the case information below, provide "
        "3 to 5 practical, numbered tips that a parent or guardian can use at home "
        "to support their child. Write in simple English at a Grade 8 reading level. "
        "Be warm, encouraging, and hopeful. Do not use clinical jargon. "
        "Do not make a diagnosis. Output maximum 250 words."
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
            max_tokens=400,
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"LiteLLM generate_parent_advice failed: {e}")
        return AI_UNAVAILABLE


async def generate_intervention_suggestions(case_id: UUID, db: AsyncSession) -> str:
    """
    Generate evidence-based intervention suggestions for clinical staff.
    Uses case info, domain scores, and existing intervention plan.
    """
    from app.assessments.models import (
        AssessmentAssignment,
        AssessmentDomain,
        AssessmentResponse,
        DomainScore,
    )
    from app.cases.models import ChildCase, InterventionPlan

    # Get case info
    case_result = await db.execute(
        select(ChildCase).where(ChildCase.id == case_id)
    )
    case = case_result.scalar_one_or_none()
    if not case:
        return "Case not found."

    # Build context
    context = "CASE INFORMATION:\n"
    context += f"Age at intake: {case.age_at_intake or 'unknown'}\n"
    context += f"Gender: {case.gender or 'unknown'}\n"
    context += f"Status: {case.status}\n"

    if case.presenting_issues:
        context += "\nPRESENTING ISSUES:\n"
        if isinstance(case.presenting_issues, list):
            context += "\n".join(f"- {issue}" for issue in case.presenting_issues)
        elif isinstance(case.presenting_issues, dict):
            for key, val in case.presenting_issues.items():
                context += f"- {key}: {val}\n"
        else:
            context += str(case.presenting_issues)

    if case.initial_diagnosis:
        context += f"\nInitial diagnosis: {case.initial_diagnosis}\n"

    # Get latest domain scores
    latest_response = await db.execute(
        select(AssessmentResponse)
        .join(AssessmentAssignment, AssessmentResponse.assignment_id == AssessmentAssignment.id)
        .where(AssessmentAssignment.case_id == case_id)
        .order_by(AssessmentResponse.completed_at.desc().nulls_last())
        .limit(1)
    )
    resp = latest_response.scalar_one_or_none()

    if resp:
        scores_result = await db.execute(
            select(DomainScore, AssessmentDomain)
            .join(AssessmentDomain, DomainScore.domain_id == AssessmentDomain.id)
            .where(DomainScore.response_id == resp.id)
        )
        rows = scores_result.all()
        if rows:
            context += "\nASSESSMENT DOMAIN SCORES:\n"
            for score, domain in rows:
                flag = " [EXCEEDS THRESHOLD]" if score.requires_further_inquiry else ""
                context += (
                    f"- {domain.domain_name}: Score {score.domain_score}"
                    f" (threshold: {domain.threshold_further_inquiry}){flag}\n"
                )

    # Get existing intervention plan
    plan_result = await db.execute(
        select(InterventionPlan)
        .where(InterventionPlan.case_id == case_id)
        .order_by(InterventionPlan.created_at.desc())
        .limit(1)
    )
    plan = plan_result.scalar_one_or_none()

    if plan:
        context += "\nEXISTING INTERVENTION PLAN:\n"
        if plan.goals:
            context += f"Goals: {json.dumps(plan.goals, default=str)}\n"
        if plan.strategies:
            context += f"Strategies: {json.dumps(plan.strategies, default=str)}\n"
        context += f"Status: {plan.status}\n"

    system_prompt = (
        "You are a clinical supervisor at a child trauma recovery centre in India. "
        "Based on the case information below, suggest 3 to 5 evidence-based therapeutic "
        "interventions. For each suggestion, briefly explain the rationale and cite the "
        "evidence base (e.g., TF-CBT, EMDR, play therapy, mindfulness, narrative therapy). "
        "Consider the child's age and cultural context. If an existing intervention plan "
        "is provided, build on it rather than contradicting it. Use professional clinical "
        "language. Output maximum 350 words."
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
            max_tokens=500,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"LiteLLM generate_intervention_suggestions failed: {e}")
        return AI_UNAVAILABLE
