"""Public Access Platform — service layer.

All functions return plain dicts (not ORM objects) to avoid
SQLAlchemy greenlet / lazy-loading issues in async endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.public.models import (
    CounselorProfile,
    PublicContent,
    Workshop,
    WorkshopRegistration,
)

# ── Indian crisis helplines (real numbers) ───────────────────────────────────

CRISIS_HELPLINES: list[dict[str, str]] = [
    {
        "name": "iCall (Psychosocial Helpline)",
        "phone": "9152987821",
        "description": "Monday–Saturday, 8 AM – 10 PM IST",
    },
    {
        "name": "Vandrevala Foundation",
        "phone": "1860-2662-345 / 9999 666 555",
        "description": "24/7, multilingual support",
    },
    {
        "name": "NIMHANS",
        "phone": "080-46110007",
        "description": "National Institute of Mental Health and Neuro-Sciences helpline",
    },
    {
        "name": "Sneha",
        "phone": "044-24640050",
        "description": "24/7 suicide prevention helpline (Chennai-based, accepts calls nationally)",
    },
    {
        "name": "Emergency Services",
        "phone": "112",
        "description": "India unified emergency number",
    },
]


# ── Wellness-check questions (anonymous, non-clinical) ──────────────────────

WELLNESS_QUESTIONS: list[dict[str, str]] = [
    {"id": "sleep", "text": "How well have you been sleeping over the past two weeks?"},
    {"id": "energy", "text": "How would you rate your energy levels most days?"},
    {"id": "mood", "text": "How would you describe your overall mood recently?"},
    {"id": "social", "text": "How connected do you feel to friends or family?"},
    {"id": "stress", "text": "How manageable does your day-to-day stress feel?"},
]


# ── Helper to serialise a row to dict ────────────────────────────────────────

def _row_to_dict(row: Any, columns: list[str]) -> dict:
    """Convert an ORM row / named-tuple to a plain dict."""
    return {col: getattr(row, col) for col in columns}


PUBLIC_CONTENT_COLS = [
    "id", "content_type", "title", "slug", "body", "tags",
    "is_published", "author_id", "published_at", "created_at", "updated_at",
]

ARTICLE_LIST_COLS = ["id", "title", "slug", "tags", "published_at"]

RESOURCE_COLS = ["id", "title", "slug", "body", "tags", "published_at"]

WORKSHOP_COLS = [
    "id", "title", "description", "facilitator_name",
    "start_datetime", "end_datetime", "location", "meeting_link",
    "capacity", "registered_count", "is_public", "registration_deadline",
    "price", "created_at",
]

REGISTRATION_COLS = [
    "id", "workshop_id", "registrant_name", "registrant_email",
    "registrant_phone", "registered_at", "attended",
]

COUNSELOR_COLS = [
    "id", "display_name", "specializations", "languages",
    "bio", "is_accepting_referrals",
]


# ─── 1. Articles ─────────────────────────────────────────────────────────────

async def list_articles(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    tag: Optional[str] = None,
) -> dict:
    """Return paginated published articles."""
    base = select(PublicContent).where(
        and_(
            PublicContent.content_type == "article",
            PublicContent.is_published.is_(True),
        )
    )

    if search:
        pattern = f"%{search}%"
        base = base.where(
            or_(
                PublicContent.title.ilike(pattern),
                PublicContent.body.ilike(pattern),
            )
        )

    if tag:
        # tags stored as JSONB array — use the @> contains operator
        base = base.where(PublicContent.tags.op("@>")(f'["{tag}"]'))

    # total count
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # page slice
    offset = (page - 1) * page_size
    rows_q = base.order_by(PublicContent.published_at.desc()).offset(offset).limit(page_size)
    rows = (await db.execute(rows_q)).scalars().all()

    items = [_row_to_dict(r, ARTICLE_LIST_COLS) for r in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_article_by_slug(slug: str, db: AsyncSession) -> Optional[dict]:
    """Fetch a single published article by its slug."""
    q = select(PublicContent).where(
        and_(
            PublicContent.slug == slug,
            PublicContent.content_type == "article",
            PublicContent.is_published.is_(True),
        )
    )
    row = (await db.execute(q)).scalars().first()
    if row is None:
        return None
    return _row_to_dict(row, PUBLIC_CONTENT_COLS)


# ─── 2. Resources ────────────────────────────────────────────────────────────

async def list_resources(
    db: AsyncSession,
    category: Optional[str] = None,
    language: Optional[str] = None,
) -> list[dict]:
    """Return published resources, optionally filtered by category tag or language tag."""
    base = select(PublicContent).where(
        and_(
            PublicContent.content_type == "resource",
            PublicContent.is_published.is_(True),
        )
    )

    if category:
        base = base.where(PublicContent.tags.op("@>")(f'["{category}"]'))

    if language:
        base = base.where(PublicContent.tags.op("@>")(f'{{"language": "{language}"}}'))

    rows = (await db.execute(base.order_by(PublicContent.title))).scalars().all()
    return [_row_to_dict(r, RESOURCE_COLS) for r in rows]


# ─── 3. Crisis info ──────────────────────────────────────────────────────────

async def get_crisis_info(db: AsyncSession) -> dict:
    """Return crisis content from the DB (if any) plus hardcoded Indian helplines."""
    q = select(PublicContent).where(
        and_(
            PublicContent.content_type == "crisis_info",
            PublicContent.is_published.is_(True),
        )
    ).order_by(PublicContent.published_at.desc())

    rows = (await db.execute(q)).scalars().all()
    content_items = [_row_to_dict(r, PUBLIC_CONTENT_COLS) for r in rows]

    return {
        "content": content_items,
        "helplines": CRISIS_HELPLINES,
    }


# ─── 4. Workshops ────────────────────────────────────────────────────────────

async def list_workshops(
    db: AsyncSession,
    upcoming_only: bool = True,
) -> list[dict]:
    """Return public workshops, optionally only upcoming ones."""
    base = select(Workshop).where(Workshop.is_public.is_(True))

    if upcoming_only:
        now = datetime.now(timezone.utc)
        base = base.where(Workshop.start_datetime >= now)

    rows = (await db.execute(base.order_by(Workshop.start_datetime))).scalars().all()
    return [_row_to_dict(r, WORKSHOP_COLS) for r in rows]


async def get_workshop(workshop_id: uuid.UUID, db: AsyncSession) -> Optional[dict]:
    """Fetch a single workshop by ID."""
    q = select(Workshop).where(Workshop.id == workshop_id)
    row = (await db.execute(q)).scalars().first()
    if row is None:
        return None
    return _row_to_dict(row, WORKSHOP_COLS)


async def register_for_workshop(
    workshop_id: uuid.UUID,
    data: dict,
    db: AsyncSession,
) -> dict:
    """Create a workshop registration and increment the registered_count."""
    # Verify workshop exists
    ws = (await db.execute(select(Workshop).where(Workshop.id == workshop_id))).scalars().first()
    if ws is None:
        raise ValueError("Workshop not found")

    # Check capacity (0 = unlimited)
    if ws.capacity > 0 and ws.registered_count >= ws.capacity:
        raise ValueError("Workshop is full")

    # Check registration deadline
    if ws.registration_deadline and datetime.now(timezone.utc) > ws.registration_deadline:
        raise ValueError("Registration deadline has passed")

    reg = WorkshopRegistration(
        id=uuid.uuid4(),
        workshop_id=workshop_id,
        registrant_name=data["registrant_name"],
        registrant_email=data.get("registrant_email"),
        registrant_phone=data.get("registrant_phone"),
    )
    db.add(reg)

    ws.registered_count = (ws.registered_count or 0) + 1
    await db.commit()
    await db.refresh(reg)

    return _row_to_dict(reg, REGISTRATION_COLS)


# ─── 5. Counselors ───────────────────────────────────────────────────────────

async def list_counselors(db: AsyncSession) -> list[dict]:
    """Return all counselor profiles that are accepting referrals."""
    q = select(CounselorProfile).where(
        CounselorProfile.is_accepting_referrals.is_(True)
    ).order_by(CounselorProfile.display_name)
    rows = (await db.execute(q)).scalars().all()
    return [_row_to_dict(r, COUNSELOR_COLS) for r in rows]


async def match_counselors(
    issues: Optional[list[str]],
    language: Optional[str],
    db: AsyncSession,
) -> list[dict]:
    """Filter counselors whose specializations overlap with `issues` and/or speak `language`."""
    base = select(CounselorProfile).where(
        CounselorProfile.is_accepting_referrals.is_(True)
    )

    if issues:
        # Match any counselor whose specializations JSONB array overlaps
        for issue in issues:
            base = base.where(
                CounselorProfile.specializations.op("@>")(f'["{issue}"]')
            )

    if language:
        base = base.where(
            CounselorProfile.languages.op("@>")(f'["{language}"]')
        )

    rows = (await db.execute(base.order_by(CounselorProfile.display_name))).scalars().all()
    return [_row_to_dict(r, COUNSELOR_COLS) for r in rows]


# ─── 6. Wellness check ───────────────────────────────────────────────────────

def get_wellness_questions() -> list[dict]:
    """Return the list of wellness-check questions (static)."""
    return WELLNESS_QUESTIONS


async def submit_wellness_check(data: dict) -> dict:
    """Process an anonymous self-assessment and return a basic result.

    This is NOT clinical scoring — it provides only general wellness tips.
    Answers are expected on a 1-5 scale (1 = poor, 5 = excellent).
    """
    answers: dict[str, int] = data.get("answers", {})
    if not answers:
        raise ValueError("No answers provided")

    values = [v for v in answers.values() if isinstance(v, (int, float))]
    if not values:
        raise ValueError("Invalid answer values")

    avg = sum(values) / len(values)

    # Simple categorisation (non-clinical)
    if avg >= 4.0:
        category = "good"
        tips = [
            "You seem to be doing well! Keep up your healthy routines.",
            "Continue nurturing your social connections.",
            "Regular exercise and good sleep are your best allies.",
        ]
    elif avg >= 2.5:
        category = "moderate"
        tips = [
            "Consider setting aside a few minutes each day for mindfulness or deep breathing.",
            "Try to maintain a consistent sleep schedule.",
            "Talking to a friend, family member, or counselor can help when things feel heavy.",
            "Small daily routines — a short walk, journaling — can build resilience over time.",
        ]
    else:
        category = "needs_attention"
        tips = [
            "It sounds like things have been tough. You are not alone.",
            "Please consider reaching out to a counselor or trusted person.",
            "Even one small step — a phone call, a short walk — can start to shift things.",
            "The crisis helplines below are available if you need immediate support.",
        ]

    return {
        "overall_score": round(avg, 2),
        "category": category,
        "tips": tips,
        "crisis_helplines": CRISIS_HELPLINES,
    }
