"""Analytics service — aggregation queries with Redis caching."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, and_, case as sql_case, extract, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from app.redis_client import redis_client

from app.cases.models import ChildCase, CaseNote, AuditLog
from app.assessments.models import (
    Assessment,
    AssessmentAssignment,
    AssessmentResponse,
    DomainScore,
    AssessmentDomain,
    RiskAlert,
)
from app.scheduling.models import Room, RoomBooking
from app.payments.models import Invoice, Payment, Donation, DonationCampaign
from app.auth.models import User

logger = logging.getLogger(__name__)

CACHE_TTL = 300  # 5 minutes


async def _get_cached(key: str):
    """Get from Redis cache."""
    try:
        data = await redis_client.get(key)
        if data:
            return json.loads(data)
    except Exception:
        pass
    return None


async def _set_cache(key: str, data, ttl: int = CACHE_TTL):
    """Set Redis cache."""
    try:
        await redis_client.set(key, json.dumps(data, default=str), ex=ttl)
    except Exception:
        pass


def _default_range(
    start: Optional[datetime], end: Optional[datetime]
) -> tuple[datetime, datetime]:
    """Return (start, end) defaulting to last 30 days in UTC."""
    if end is None:
        end = datetime.now(timezone.utc)
    if start is None:
        start = end - timedelta(days=30)
    return start, end


def _cache_key(prefix: str, start: datetime, end: datetime) -> str:
    """Build a deterministic cache key from prefix and date range."""
    return f"analytics:{prefix}:{start.isoformat()}:{end.isoformat()}"


# ---------------------------------------------------------------------------
# 1. Overview — summary cards
# ---------------------------------------------------------------------------

async def get_overview(
    db: AsyncSession,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> dict:
    start, end = _default_range(start, end)
    key = _cache_key("overview", start, end)
    cached = await _get_cached(key)
    if cached:
        return cached

    period_length = end - start
    prev_end = start
    prev_start = prev_end - period_length

    # Active cases (current snapshot — not date-bounded)
    result = await db.execute(
        select(func.count()).select_from(ChildCase).where(ChildCase.status == "active")
    )
    active_cases = result.scalar() or 0

    # Previous-period active cases approximation: cases created before prev_end
    # that were active (not closed before prev_end)
    result = await db.execute(
        select(func.count())
        .select_from(ChildCase)
        .where(
            and_(
                ChildCase.created_at <= prev_end,
                ChildCase.status.in_(["active", "on_hold"]),
            )
        )
    )
    prev_active = result.scalar() or 0

    # Assessments this month (current period)
    result = await db.execute(
        select(func.count())
        .select_from(AssessmentResponse)
        .where(
            and_(
                AssessmentResponse.completed_at >= start,
                AssessmentResponse.completed_at <= end,
            )
        )
    )
    assessments_period = result.scalar() or 0

    result = await db.execute(
        select(func.count())
        .select_from(AssessmentResponse)
        .where(
            and_(
                AssessmentResponse.completed_at >= prev_start,
                AssessmentResponse.completed_at <= prev_end,
            )
        )
    )
    assessments_prev = result.scalar() or 0

    # P0 alerts in period
    result = await db.execute(
        select(func.count())
        .select_from(RiskAlert)
        .where(
            and_(
                RiskAlert.severity == "P0",
                RiskAlert.created_at >= start,
                RiskAlert.created_at <= end,
            )
        )
    )
    p0_alerts = result.scalar() or 0

    result = await db.execute(
        select(func.count())
        .select_from(RiskAlert)
        .where(
            and_(
                RiskAlert.severity == "P0",
                RiskAlert.created_at >= prev_start,
                RiskAlert.created_at <= prev_end,
            )
        )
    )
    p0_prev = result.scalar() or 0

    # Total donated in period (captured)
    result = await db.execute(
        select(func.coalesce(func.sum(Donation.amount), 0))
        .where(
            and_(
                Donation.status == "captured",
                Donation.created_at >= start,
                Donation.created_at <= end,
            )
        )
    )
    total_donated = float(result.scalar() or 0)

    result = await db.execute(
        select(func.coalesce(func.sum(Donation.amount), 0))
        .where(
            and_(
                Donation.status == "captured",
                Donation.created_at >= prev_start,
                Donation.created_at <= prev_end,
            )
        )
    )
    donated_prev = float(result.scalar() or 0)

    def pct_change(current, previous):
        if previous == 0:
            return 100.0 if current > 0 else 0.0
        return round(((current - previous) / previous) * 100, 1)

    data = {
        "active_cases": active_cases,
        "active_cases_change_pct": pct_change(active_cases, prev_active),
        "assessments_period": assessments_period,
        "assessments_change_pct": pct_change(assessments_period, assessments_prev),
        "p0_alerts": p0_alerts,
        "p0_alerts_change_pct": pct_change(p0_alerts, p0_prev),
        "total_donated": total_donated,
        "total_donated_change_pct": pct_change(total_donated, donated_prev),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }

    await _set_cache(key, data)
    return data


# ---------------------------------------------------------------------------
# 2. Case volume
# ---------------------------------------------------------------------------

async def get_case_volume(
    db: AsyncSession,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> dict:
    start, end = _default_range(start, end)
    key = _cache_key("case_volume", start, end)
    cached = await _get_cached(key)
    if cached:
        return cached

    # New cases per month (last 12 months from end)
    twelve_months_ago = end - timedelta(days=365)
    result = await db.execute(
        select(
            extract("year", ChildCase.created_at).label("year"),
            extract("month", ChildCase.created_at).label("month"),
            func.count().label("count"),
        )
        .where(ChildCase.created_at >= twelve_months_ago)
        .group_by("year", "month")
        .order_by("year", "month")
    )
    new_cases_per_month = [
        {"year": int(row.year), "month": int(row.month), "count": row.count}
        for row in result.all()
    ]

    # Cases by status
    result = await db.execute(
        select(
            ChildCase.status,
            func.count().label("count"),
        ).group_by(ChildCase.status)
    )
    cases_by_status = [
        {"status": row.status, "count": row.count}
        for row in result.all()
    ]

    # Active cases total
    result = await db.execute(
        select(func.count()).select_from(ChildCase).where(ChildCase.status == "active")
    )
    active_cases_total = result.scalar() or 0

    data = {
        "new_cases_per_month": new_cases_per_month,
        "cases_by_status": cases_by_status,
        "active_cases_total": active_cases_total,
    }

    await _set_cache(key, data)
    return data


# ---------------------------------------------------------------------------
# 3. Assessment trends
# ---------------------------------------------------------------------------

async def get_assessment_trends(
    db: AsyncSession,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> dict:
    start, end = _default_range(start, end)
    key = _cache_key("assessment_trends", start, end)
    cached = await _get_cached(key)
    if cached:
        return cached

    # Assessments per month
    twelve_months_ago = end - timedelta(days=365)
    result = await db.execute(
        select(
            extract("year", AssessmentResponse.completed_at).label("year"),
            extract("month", AssessmentResponse.completed_at).label("month"),
            func.count().label("count"),
        )
        .where(
            and_(
                AssessmentResponse.completed_at.isnot(None),
                AssessmentResponse.completed_at >= twelve_months_ago,
            )
        )
        .group_by("year", "month")
        .order_by("year", "month")
    )
    assessments_per_month = [
        {"year": int(row.year), "month": int(row.month), "count": row.count}
        for row in result.all()
    ]

    # Average domain scores by domain name
    result = await db.execute(
        select(
            AssessmentDomain.domain_name,
            func.avg(DomainScore.domain_score).label("avg_score"),
        )
        .join(AssessmentDomain, DomainScore.domain_id == AssessmentDomain.id)
        .join(AssessmentResponse, DomainScore.response_id == AssessmentResponse.id)
        .where(
            and_(
                AssessmentResponse.completed_at >= start,
                AssessmentResponse.completed_at <= end,
            )
        )
        .group_by(AssessmentDomain.domain_name)
        .order_by(AssessmentDomain.domain_name)
    )
    avg_domain_scores = [
        {"domain_name": row.domain_name, "avg_score": round(float(row.avg_score), 2)}
        for row in result.all()
    ]

    # Percentage flagged — responses with at least one DomainScore where
    # requires_further_inquiry is True
    total_responses_q = (
        select(func.count(distinct(AssessmentResponse.id)))
        .where(
            and_(
                AssessmentResponse.completed_at >= start,
                AssessmentResponse.completed_at <= end,
                AssessmentResponse.completed_at.isnot(None),
            )
        )
    )
    result = await db.execute(total_responses_q)
    total_responses = result.scalar() or 0

    flagged_q = (
        select(func.count(distinct(DomainScore.response_id)))
        .join(AssessmentResponse, DomainScore.response_id == AssessmentResponse.id)
        .where(
            and_(
                DomainScore.requires_further_inquiry.is_(True),
                AssessmentResponse.completed_at >= start,
                AssessmentResponse.completed_at <= end,
            )
        )
    )
    result = await db.execute(flagged_q)
    flagged_count = result.scalar() or 0

    pct_flagged = round((flagged_count / total_responses * 100), 1) if total_responses > 0 else 0.0

    # P0 alerts per month
    result = await db.execute(
        select(
            extract("year", RiskAlert.created_at).label("year"),
            extract("month", RiskAlert.created_at).label("month"),
            func.count().label("count"),
        )
        .where(
            and_(
                RiskAlert.severity == "P0",
                RiskAlert.created_at >= twelve_months_ago,
            )
        )
        .group_by("year", "month")
        .order_by("year", "month")
    )
    p0_alerts_per_month = [
        {"year": int(row.year), "month": int(row.month), "count": row.count}
        for row in result.all()
    ]

    data = {
        "assessments_per_month": assessments_per_month,
        "avg_domain_scores": avg_domain_scores,
        "pct_flagged": pct_flagged,
        "p0_alerts_per_month": p0_alerts_per_month,
    }

    await _set_cache(key, data)
    return data


# ---------------------------------------------------------------------------
# 4. Room utilisation
# ---------------------------------------------------------------------------

async def get_room_utilisation(
    db: AsyncSession,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> dict:
    start, end = _default_range(start, end)
    key = _cache_key("room_utilisation", start, end)
    cached = await _get_cached(key)
    if cached:
        return cached

    # Fetch all active rooms
    result = await db.execute(
        select(Room).where(Room.is_active.is_(True))
    )
    rooms = result.scalars().all()

    # Calculate available hours: 12h/day, 5 days/week over the period
    total_days = (end - start).days or 1
    total_weeks = total_days / 7
    available_hours_per_room = total_weeks * 5 * 12  # 12h/day, 5 days/week

    # Booked hours per room (confirmed or completed bookings in range)
    result = await db.execute(
        select(
            RoomBooking.room_id,
            func.sum(
                extract("epoch", RoomBooking.end_datetime - RoomBooking.start_datetime) / 3600
            ).label("booked_hours"),
            func.count().label("booking_count"),
        )
        .where(
            and_(
                RoomBooking.start_datetime >= start,
                RoomBooking.end_datetime <= end,
                RoomBooking.status.in_(["confirmed", "completed"]),
            )
        )
        .group_by(RoomBooking.room_id)
    )
    room_bookings = {row.room_id: {"hours": float(row.booked_hours or 0), "count": row.booking_count} for row in result.all()}

    utilisation_per_room = []
    for room in rooms:
        booked = room_bookings.get(room.id, {"hours": 0, "count": 0})
        util_pct = round((booked["hours"] / available_hours_per_room * 100), 1) if available_hours_per_room > 0 else 0.0
        utilisation_per_room.append({
            "room_id": str(room.id),
            "room_name": room.name,
            "booked_hours": round(booked["hours"], 1),
            "available_hours": round(available_hours_per_room, 1),
            "utilisation_pct": util_pct,
        })

    # Most popular rooms — top 5 by booking count
    result = await db.execute(
        select(
            Room.name,
            func.count().label("booking_count"),
        )
        .join(RoomBooking, RoomBooking.room_id == Room.id)
        .where(
            and_(
                RoomBooking.start_datetime >= start,
                RoomBooking.end_datetime <= end,
                RoomBooking.status.in_(["confirmed", "completed"]),
            )
        )
        .group_by(Room.name)
        .order_by(func.count().desc())
        .limit(5)
    )
    most_popular_rooms = [
        {"room_name": row.name, "booking_count": row.booking_count}
        for row in result.all()
    ]

    # Booking types
    result = await db.execute(
        select(
            RoomBooking.booking_type,
            func.count().label("count"),
        )
        .where(
            and_(
                RoomBooking.start_datetime >= start,
                RoomBooking.end_datetime <= end,
                RoomBooking.status.in_(["confirmed", "completed"]),
            )
        )
        .group_by(RoomBooking.booking_type)
    )
    booking_types = [
        {"booking_type": row.booking_type, "count": row.count}
        for row in result.all()
    ]

    # Peak hours heatmap — day_of_week (0=Sun..6=Sat) and hour (8-20)
    # PostgreSQL: extract(dow ...) gives 0=Sunday, extract(hour ...) gives 0-23
    result = await db.execute(
        select(
            extract("dow", RoomBooking.start_datetime).label("day_of_week"),
            extract("hour", RoomBooking.start_datetime).label("hour"),
            func.count().label("count"),
        )
        .where(
            and_(
                RoomBooking.start_datetime >= start,
                RoomBooking.end_datetime <= end,
                RoomBooking.status.in_(["confirmed", "completed"]),
                extract("hour", RoomBooking.start_datetime) >= 8,
                extract("hour", RoomBooking.start_datetime) <= 20,
            )
        )
        .group_by("day_of_week", "hour")
        .order_by("day_of_week", "hour")
    )
    peak_hours = [
        {"day_of_week": int(row.day_of_week), "hour": int(row.hour), "count": row.count}
        for row in result.all()
    ]

    data = {
        "utilisation_per_room": utilisation_per_room,
        "most_popular_rooms": most_popular_rooms,
        "booking_types": booking_types,
        "peak_hours": peak_hours,
    }

    await _set_cache(key, data)
    return data


# ---------------------------------------------------------------------------
# 5. Staff activity
# ---------------------------------------------------------------------------

async def get_staff_activity(
    db: AsyncSession,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> dict:
    start, end = _default_range(start, end)
    key = _cache_key("staff_activity", start, end)
    cached = await _get_cached(key)
    if cached:
        return cached

    # Notes per therapist (author_id is the FK on CaseNote)
    result = await db.execute(
        select(
            User.full_name,
            CaseNote.author_id,
            func.count().label("note_count"),
        )
        .join(User, CaseNote.author_id == User.id)
        .where(
            and_(
                CaseNote.created_at >= start,
                CaseNote.created_at <= end,
            )
        )
        .group_by(User.full_name, CaseNote.author_id)
        .order_by(func.count().desc())
    )
    notes_per_therapist = [
        {
            "therapist_id": str(row.author_id),
            "therapist_name": row.full_name,
            "note_count": row.note_count,
        }
        for row in result.all()
    ]

    # Sessions per therapist (note_type = 'session')
    result = await db.execute(
        select(
            User.full_name,
            CaseNote.author_id,
            func.count().label("session_count"),
        )
        .join(User, CaseNote.author_id == User.id)
        .where(
            and_(
                CaseNote.note_type == "session",
                CaseNote.created_at >= start,
                CaseNote.created_at <= end,
            )
        )
        .group_by(User.full_name, CaseNote.author_id)
        .order_by(func.count().desc())
    )
    sessions_per_therapist = [
        {
            "therapist_id": str(row.author_id),
            "therapist_name": row.full_name,
            "session_count": row.session_count,
        }
        for row in result.all()
    ]

    # Average distinct cases per therapist
    result = await db.execute(
        select(
            User.full_name,
            CaseNote.author_id,
            func.count(distinct(CaseNote.case_id)).label("case_count"),
        )
        .join(User, CaseNote.author_id == User.id)
        .where(
            and_(
                CaseNote.created_at >= start,
                CaseNote.created_at <= end,
            )
        )
        .group_by(User.full_name, CaseNote.author_id)
        .order_by(func.count(distinct(CaseNote.case_id)).desc())
    )
    cases_rows = result.all()
    cases_per_therapist = [
        {
            "therapist_id": str(row.author_id),
            "therapist_name": row.full_name,
            "case_count": row.case_count,
        }
        for row in cases_rows
    ]
    avg_cases = (
        round(sum(r.case_count for r in cases_rows) / len(cases_rows), 1)
        if cases_rows
        else 0.0
    )

    data = {
        "notes_per_therapist": notes_per_therapist,
        "sessions_per_therapist": sessions_per_therapist,
        "cases_per_therapist": cases_per_therapist,
        "avg_cases_per_therapist": avg_cases,
    }

    await _set_cache(key, data)
    return data


# ---------------------------------------------------------------------------
# 6. Donation analytics
# ---------------------------------------------------------------------------

async def get_donation_analytics(
    db: AsyncSession,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> dict:
    start, end = _default_range(start, end)
    key = _cache_key("donation_analytics", start, end)
    cached = await _get_cached(key)
    if cached:
        return cached

    base_filter = and_(
        Donation.status == "captured",
        Donation.created_at >= start,
        Donation.created_at <= end,
    )

    # Total raised
    result = await db.execute(
        select(func.coalesce(func.sum(Donation.amount), 0)).where(base_filter)
    )
    total_raised = float(result.scalar() or 0)

    # Donor count (distinct emails)
    result = await db.execute(
        select(func.count(distinct(Donation.donor_email))).where(base_filter)
    )
    donor_count = result.scalar() or 0

    # Average donation
    result = await db.execute(
        select(func.avg(Donation.amount)).where(base_filter)
    )
    avg_donation = round(float(result.scalar() or 0), 2)

    # Recurring vs one-time
    result = await db.execute(
        select(
            Donation.is_recurring,
            func.count().label("count"),
            func.coalesce(func.sum(Donation.amount), 0).label("total"),
        )
        .where(base_filter)
        .group_by(Donation.is_recurring)
    )
    recurring_vs_onetime = [
        {
            "is_recurring": row.is_recurring,
            "count": row.count,
            "total": float(row.total),
        }
        for row in result.all()
    ]

    # By gateway
    result = await db.execute(
        select(
            Donation.gateway,
            func.count().label("count"),
            func.coalesce(func.sum(Donation.amount), 0).label("total"),
        )
        .where(base_filter)
        .group_by(Donation.gateway)
    )
    by_gateway = [
        {
            "gateway": row.gateway or "unknown",
            "count": row.count,
            "total": float(row.total),
        }
        for row in result.all()
    ]

    data = {
        "total_raised": total_raised,
        "donor_count": donor_count,
        "avg_donation": avg_donation,
        "recurring_vs_onetime": recurring_vs_onetime,
        "by_gateway": by_gateway,
    }

    await _set_cache(key, data)
    return data


# ---------------------------------------------------------------------------
# 7. Program effectiveness — domain score improvement (first vs latest)
# ---------------------------------------------------------------------------

async def get_program_effectiveness(
    db: AsyncSession,
) -> dict:
    key = "analytics:program_effectiveness"
    cached = await _get_cached(key)
    if cached:
        return cached

    # Find cases with 2+ completed assessment responses.
    # Path: AssessmentResponse -> AssessmentAssignment -> case_id
    # We need first and latest response per case, then compare domain scores.

    # Subquery: responses with case_id and completion order
    responses_with_case = (
        select(
            AssessmentResponse.id.label("response_id"),
            AssessmentAssignment.case_id.label("case_id"),
            AssessmentResponse.completed_at.label("completed_at"),
            func.row_number()
            .over(
                partition_by=AssessmentAssignment.case_id,
                order_by=AssessmentResponse.completed_at.asc(),
            )
            .label("rn_asc"),
            func.row_number()
            .over(
                partition_by=AssessmentAssignment.case_id,
                order_by=AssessmentResponse.completed_at.desc(),
            )
            .label("rn_desc"),
            func.count()
            .over(partition_by=AssessmentAssignment.case_id)
            .label("total_responses"),
        )
        .join(
            AssessmentAssignment,
            AssessmentResponse.assignment_id == AssessmentAssignment.id,
        )
        .where(AssessmentResponse.completed_at.isnot(None))
        .subquery()
    )

    # First responses (rn_asc=1 and total_responses >= 2)
    first_responses = (
        select(
            responses_with_case.c.response_id,
            responses_with_case.c.case_id,
        )
        .where(
            and_(
                responses_with_case.c.rn_asc == 1,
                responses_with_case.c.total_responses >= 2,
            )
        )
        .subquery("first_responses")
    )

    # Latest responses (rn_desc=1 and total_responses >= 2)
    latest_responses = (
        select(
            responses_with_case.c.response_id,
            responses_with_case.c.case_id,
        )
        .where(
            and_(
                responses_with_case.c.rn_desc == 1,
                responses_with_case.c.total_responses >= 2,
            )
        )
        .subquery("latest_responses")
    )

    # Average first scores per domain
    first_scores_q = await db.execute(
        select(
            AssessmentDomain.domain_name,
            func.avg(DomainScore.domain_score).label("avg_score"),
            func.count(distinct(first_responses.c.case_id)).label("case_count"),
        )
        .join(first_responses, DomainScore.response_id == first_responses.c.response_id)
        .join(AssessmentDomain, DomainScore.domain_id == AssessmentDomain.id)
        .group_by(AssessmentDomain.domain_name)
    )
    first_scores = {
        row.domain_name: {"avg": float(row.avg_score), "case_count": row.case_count}
        for row in first_scores_q.all()
    }

    # Average latest scores per domain
    latest_scores_q = await db.execute(
        select(
            AssessmentDomain.domain_name,
            func.avg(DomainScore.domain_score).label("avg_score"),
        )
        .join(latest_responses, DomainScore.response_id == latest_responses.c.response_id)
        .join(AssessmentDomain, DomainScore.domain_id == AssessmentDomain.id)
        .group_by(AssessmentDomain.domain_name)
    )
    latest_scores = {
        row.domain_name: float(row.avg_score)
        for row in latest_scores_q.all()
    }

    # Merge into per-domain results
    domains = []
    for domain_name in sorted(set(first_scores.keys()) | set(latest_scores.keys())):
        first_avg = first_scores.get(domain_name, {}).get("avg", 0.0)
        latest_avg = latest_scores.get(domain_name, 0.0)
        case_count = first_scores.get(domain_name, {}).get("case_count", 0)
        improvement = round(latest_avg - first_avg, 2)
        domains.append({
            "domain_name": domain_name,
            "first_avg": round(first_avg, 2),
            "latest_avg": round(latest_avg, 2),
            "improvement": improvement,
            "case_count": case_count,
        })

    data = {"domains": domains}

    await _set_cache(key, data)
    return data
