"""Public Access Platform — API endpoints.

All endpoints are public (no authentication required).
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.public.schemas import (
    WellnessCheckSubmission,
    WorkshopRegistrationCreate,
)
from app.public import service

router = APIRouter()


# ── Articles ─────────────────────────────────────────────────────────────────

@router.get("/articles")
async def list_articles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_articles(db, page=page, page_size=page_size, search=search, tag=tag)


@router.get("/articles/{slug}")
async def get_article(slug: str, db: AsyncSession = Depends(get_db)):
    article = await service.get_article_by_slug(slug, db)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


# ── Resources ────────────────────────────────────────────────────────────────

@router.get("/resources")
async def list_resources(
    category: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_resources(db, category=category, language=language)


# ── Crisis info ──────────────────────────────────────────────────────────────

@router.get("/crisis")
async def get_crisis_info(db: AsyncSession = Depends(get_db)):
    return await service.get_crisis_info(db)


# ── Workshops ────────────────────────────────────────────────────────────────

@router.get("/workshops")
async def list_workshops(
    upcoming_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_workshops(db, upcoming_only=upcoming_only)


@router.get("/workshops/{workshop_id}")
async def get_workshop(workshop_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    ws = await service.get_workshop(workshop_id, db)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workshop not found")
    return ws


@router.post("/workshops/{workshop_id}/register", status_code=201)
async def register_for_workshop(
    workshop_id: uuid.UUID,
    data: WorkshopRegistrationCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await service.register_for_workshop(workshop_id, data.model_dump(), db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Counselors ───────────────────────────────────────────────────────────────

@router.get("/counselors")
async def list_counselors(db: AsyncSession = Depends(get_db)):
    return await service.list_counselors(db)


@router.get("/counselors/match")
async def match_counselors(
    issues: Optional[str] = Query(None, description="Comma-separated list of issues"),
    language: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    issues_list = [i.strip() for i in issues.split(",") if i.strip()] if issues else None
    return await service.match_counselors(issues_list, language, db)


# ── Wellness check ───────────────────────────────────────────────────────────

@router.get("/wellness-check")
async def get_wellness_questions():
    return {"questions": service.get_wellness_questions()}


@router.post("/wellness-check/submit")
async def submit_wellness_check(data: WellnessCheckSubmission):
    try:
        return await service.submit_wellness_check(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
