"""Case management business logic — CRUD, assignments, notes, interventions, milestones."""

from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.models import User
from app.cases.models import (
    CaseAssignment,
    CaseNote,
    ChildCase,
    InterventionPlan,
    ProgressMilestone,
)
from app.cases.schemas import (
    AssignmentOut,
    CaseCreate,
    CaseListItem,
    CaseOut,
    CaseUpdate,
    InterventionCreate,
    InterventionOut,
    InterventionUpdate,
    MilestoneCreate,
    MilestoneOut,
    NoteCreate,
    NoteOut,
    NoteUpdate,
    TimelineEvent,
)
from app.security.encryption import decrypt_field, encrypt_field


# ---------------------------------------------------------------------------
# Case number generation
# ---------------------------------------------------------------------------

async def _generate_case_number(db: AsyncSession) -> str:
    """Generate next case number: ARK-{YEAR}-{5-digit-sequence}."""
    year = datetime.now(timezone.utc).year
    # Use a count-based approach for case number generation
    result = await db.execute(
        select(func.count(ChildCase.id)).where(
            ChildCase.case_number.like(f"ARK-{year}-%")
        )
    )
    count = (result.scalar() or 0) + 1
    return f"ARK-{year}-{count:05d}"


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

_ENCRYPTED_FIELDS = [
    "first_name", "last_name", "date_of_birth",
    "guardian_name", "guardian_phone", "guardian_email", "address",
]


def _encrypt_case_fields(data: dict) -> dict:
    """Encrypt PII fields before writing to DB."""
    for field in _ENCRYPTED_FIELDS:
        if field in data and data[field] is not None:
            data[field] = encrypt_field(str(data[field]))
    return data


def _case_to_dict(case: ChildCase) -> dict:
    """Convert a ChildCase ORM object to a dict with decrypted PII fields.

    This avoids modifying the session-bound ORM instance (which causes
    MissingGreenlet errors in async contexts).
    """
    data = {
        "id": case.id,
        "case_number": case.case_number,
        "first_name": case.first_name,
        "last_name": case.last_name,
        "date_of_birth": case.date_of_birth,
        "gender": case.gender,
        "age_at_intake": case.age_at_intake,
        "guardian_name": case.guardian_name,
        "guardian_phone": case.guardian_phone,
        "guardian_email": case.guardian_email,
        "guardian_relationship": case.guardian_relationship,
        "address": case.address,
        "school_name": case.school_name,
        "referral_source": case.referral_source,
        "presenting_issues": case.presenting_issues,
        "initial_diagnosis": case.initial_diagnosis,
        "status": case.status,
        "intake_date": case.intake_date,
        "closed_date": case.closed_date,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }
    # Decrypt PII fields
    for field in _ENCRYPTED_FIELDS:
        if data.get(field) is not None:
            try:
                data[field] = decrypt_field(data[field])
            except Exception:
                pass  # Already plaintext or test env
    return data


# ---------------------------------------------------------------------------
# Case CRUD
# ---------------------------------------------------------------------------

async def create_case(data: CaseCreate, user_id: UUID, db: AsyncSession) -> dict:
    case_number = await _generate_case_number(db)
    fields = data.model_dump(exclude_none=True)

    # Set intake_date default before encryption (and remove from fields to avoid duplicate kwarg)
    if "intake_date" not in fields or fields["intake_date"] is None:
        fields["intake_date"] = date.today()

    # Encrypt PII fields
    fields = _encrypt_case_fields(fields)

    case = ChildCase(
        case_number=case_number,
        created_by=user_id,
        **fields,
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return _case_to_dict(case)


async def get_case(case_id: UUID, db: AsyncSession) -> Optional[dict]:
    result = await db.execute(
        select(ChildCase).where(ChildCase.id == case_id)
    )
    case = result.scalar_one_or_none()
    if case:
        return _case_to_dict(case)
    return None


async def list_cases(
    db: AsyncSession,
    user_id: Optional[UUID] = None,
    user_role: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    therapist_id: Optional[UUID] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CaseListItem], int]:
    """List cases with ABAC filtering, search, and pagination."""
    query = select(ChildCase)
    count_query = select(func.count(ChildCase.id))

    # ABAC: non-admin roles only see assigned cases
    bypass_roles = {"super_admin", "admin", "chief_therapist"}
    if user_role and user_role not in bypass_roles and user_id:
        assigned_case_ids = select(CaseAssignment.case_id).where(
            CaseAssignment.user_id == user_id,
            CaseAssignment.is_active == True,  # noqa: E712
        )
        query = query.where(ChildCase.id.in_(assigned_case_ids))
        count_query = count_query.where(ChildCase.id.in_(assigned_case_ids))

    if status:
        query = query.where(ChildCase.status == status)
        count_query = count_query.where(ChildCase.status == status)

    if therapist_id:
        therapist_cases = select(CaseAssignment.case_id).where(
            CaseAssignment.user_id == therapist_id,
            CaseAssignment.is_active == True,  # noqa: E712
        )
        query = query.where(ChildCase.id.in_(therapist_cases))
        count_query = count_query.where(ChildCase.id.in_(therapist_cases))

    if search:
        # Search by case number (always searchable) or encrypted name fields
        query = query.where(ChildCase.case_number.ilike(f"%{search}%"))
        count_query = count_query.where(ChildCase.case_number.ilike(f"%{search}%"))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(ChildCase.created_at.desc())
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    cases = result.scalars().all()

    items = []
    for case in cases:
        case_data = _case_to_dict(case)

        # Get assigned therapist name
        therapist_name = None
        therapist_result = await db.execute(
            select(CaseAssignment).where(
                CaseAssignment.case_id == case.id,
                CaseAssignment.assignment_type == "primary_therapist",
                CaseAssignment.is_active == True,  # noqa: E712
            )
        )
        assignment = therapist_result.scalar_one_or_none()
        if assignment:
            user_result = await db.execute(
                select(User).where(User.id == assignment.user_id)
            )
            therapist = user_result.scalar_one_or_none()
            if therapist:
                therapist_name = therapist.full_name

        # Get last activity (most recent note)
        last_note_result = await db.execute(
            select(CaseNote.created_at)
            .where(CaseNote.case_id == case.id)
            .order_by(CaseNote.created_at.desc())
            .limit(1)
        )
        last_activity = last_note_result.scalar_one_or_none()

        items.append(CaseListItem(
            id=case_data["id"],
            case_number=case_data["case_number"],
            first_name=case_data["first_name"],
            last_name=case_data["last_name"],
            age_at_intake=case_data["age_at_intake"],
            status=case_data["status"],
            intake_date=case_data["intake_date"],
            created_at=case_data["created_at"],
            assigned_therapist=therapist_name,
            last_activity=last_activity,
        ))

    return items, total


async def update_case(case_id: UUID, data: CaseUpdate, db: AsyncSession) -> Optional[dict]:
    result = await db.execute(
        select(ChildCase).where(ChildCase.id == case_id)
    )
    case = result.scalar_one_or_none()
    if not case:
        return None

    updates = data.model_dump(exclude_none=True)
    # Re-encrypt PII fields that are being updated
    updates = _encrypt_case_fields(updates)

    for field, value in updates.items():
        setattr(case, field, value)

    if data.status == "closed":
        case.closed_date = date.today()

    await db.commit()
    await db.refresh(case)
    return _case_to_dict(case)


async def soft_delete_case(case_id: UUID, db: AsyncSession) -> Optional[dict]:
    result = await db.execute(
        select(ChildCase).where(ChildCase.id == case_id)
    )
    case = result.scalar_one_or_none()
    if not case:
        return None
    case.status = "closed"
    case.closed_date = date.today()
    await db.commit()
    await db.refresh(case)
    return _case_to_dict(case)


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

async def create_assignment(
    case_id: UUID, user_id: UUID, assignment_type: str, assigned_by: UUID, db: AsyncSession
) -> CaseAssignment:
    assignment = CaseAssignment(
        case_id=case_id,
        user_id=user_id,
        assignment_type=assignment_type,
        assigned_by=assigned_by,
        is_active=True,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


async def delete_assignment(assignment_id: UUID, db: AsyncSession) -> bool:
    result = await db.execute(
        select(CaseAssignment).where(CaseAssignment.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        return False
    assignment.is_active = False
    await db.commit()
    return True


async def list_assignments(case_id: UUID, db: AsyncSession) -> list[AssignmentOut]:
    result = await db.execute(
        select(CaseAssignment).where(
            CaseAssignment.case_id == case_id,
            CaseAssignment.is_active == True,  # noqa: E712
        )
    )
    assignments = result.scalars().all()
    items = []
    for a in assignments:
        user_result = await db.execute(select(User).where(User.id == a.user_id))
        user = user_result.scalar_one_or_none()
        items.append(AssignmentOut(
            id=a.id,
            case_id=a.case_id,
            user_id=a.user_id,
            assignment_type=a.assignment_type,
            assigned_at=a.assigned_at,
            assigned_by=a.assigned_by,
            is_active=a.is_active,
            user_name=user.full_name if user else None,
        ))
    return items


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

async def create_note(
    case_id: UUID, author_id: UUID, data: NoteCreate, db: AsyncSession
) -> CaseNote:
    note = CaseNote(
        case_id=case_id,
        author_id=author_id,
        note_type=data.note_type,
        content=encrypt_field(data.content),
        session_date=data.session_date,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


async def list_notes(case_id: UUID, db: AsyncSession) -> list[NoteOut]:
    result = await db.execute(
        select(CaseNote)
        .where(CaseNote.case_id == case_id)
        .order_by(CaseNote.created_at.desc())
    )
    notes = result.scalars().all()
    items = []
    for n in notes:
        user_result = await db.execute(select(User).where(User.id == n.author_id))
        user = user_result.scalar_one_or_none()
        try:
            content = decrypt_field(n.content)
        except Exception:
            content = n.content
        items.append(NoteOut(
            id=n.id,
            case_id=n.case_id,
            author_id=n.author_id,
            note_type=n.note_type,
            content=content,
            session_date=n.session_date,
            created_at=n.created_at,
            updated_at=n.updated_at,
            author_name=user.full_name if user else None,
        ))
    return items


async def update_note(
    note_id: UUID, data: NoteUpdate, db: AsyncSession
) -> Optional[CaseNote]:
    result = await db.execute(select(CaseNote).where(CaseNote.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        return None
    if data.content is not None:
        note.content = encrypt_field(data.content)
    if data.note_type is not None:
        note.note_type = data.note_type
    if data.session_date is not None:
        note.session_date = data.session_date
    await db.commit()
    await db.refresh(note)
    return note


# ---------------------------------------------------------------------------
# Intervention Plans
# ---------------------------------------------------------------------------

async def create_intervention(
    case_id: UUID, user_id: UUID, data: InterventionCreate, db: AsyncSession
) -> InterventionPlan:
    plan = InterventionPlan(
        case_id=case_id,
        created_by=user_id,
        goals=data.goals,
        strategies=data.strategies,
        review_date=data.review_date,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


async def list_interventions(case_id: UUID, db: AsyncSession) -> list[InterventionOut]:
    result = await db.execute(
        select(InterventionPlan)
        .where(InterventionPlan.case_id == case_id)
        .order_by(InterventionPlan.created_at.desc())
    )
    plans = result.scalars().all()
    return [InterventionOut.model_validate(p) for p in plans]


async def update_intervention(
    plan_id: UUID, data: InterventionUpdate, db: AsyncSession
) -> Optional[InterventionPlan]:
    result = await db.execute(
        select(InterventionPlan).where(InterventionPlan.id == plan_id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        return None
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(plan, field, value)
    await db.commit()
    await db.refresh(plan)
    return plan


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------

async def create_milestone(
    case_id: UUID, user_id: UUID, data: MilestoneCreate, db: AsyncSession
) -> ProgressMilestone:
    milestone = ProgressMilestone(
        case_id=case_id,
        recorded_by=user_id,
        milestone_text=data.milestone_text,
        milestone_date=data.milestone_date or date.today(),
        domain=data.domain,
    )
    db.add(milestone)
    await db.commit()
    await db.refresh(milestone)
    return milestone


async def list_milestones(case_id: UUID, db: AsyncSession) -> list[MilestoneOut]:
    result = await db.execute(
        select(ProgressMilestone)
        .where(ProgressMilestone.case_id == case_id)
        .order_by(ProgressMilestone.milestone_date.desc())
    )
    milestones = result.scalars().all()
    return [MilestoneOut.model_validate(m) for m in milestones]


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

async def get_timeline(case_id: UUID, db: AsyncSession) -> list[TimelineEvent]:
    """Full chronological timeline for a case: notes, milestones, assessments."""
    events: list[TimelineEvent] = []

    # Notes
    notes_result = await db.execute(
        select(CaseNote).where(CaseNote.case_id == case_id)
    )
    for note in notes_result.scalars().all():
        try:
            content = decrypt_field(note.content)
        except Exception:
            content = note.content
        events.append(TimelineEvent(
            event_type="note",
            event_date=note.created_at,
            title=f"{note.note_type.title()} Note",
            description=content[:200] + "..." if len(content) > 200 else content,
            metadata={"note_id": str(note.id), "note_type": note.note_type},
        ))

    # Milestones
    milestones_result = await db.execute(
        select(ProgressMilestone).where(ProgressMilestone.case_id == case_id)
    )
    for ms in milestones_result.scalars().all():
        events.append(TimelineEvent(
            event_type="milestone",
            event_date=datetime.combine(
                ms.milestone_date or date.today(),
                datetime.min.time(),
                tzinfo=timezone.utc,
            ) if ms.milestone_date else datetime.now(timezone.utc),
            title=f"Milestone: {ms.domain or 'General'}",
            description=ms.milestone_text,
            metadata={"milestone_id": str(ms.id), "domain": ms.domain},
        ))

    # Assessment responses
    from app.assessments.models import AssessmentAssignment, AssessmentResponse, Assessment
    assignments_result = await db.execute(
        select(AssessmentAssignment).where(AssessmentAssignment.case_id == case_id)
    )
    for assignment in assignments_result.scalars().all():
        responses_result = await db.execute(
            select(AssessmentResponse).where(
                AssessmentResponse.assignment_id == assignment.id,
                AssessmentResponse.is_partial == False,  # noqa: E712
            )
        )
        for resp in responses_result.scalars().all():
            assessment_result = await db.execute(
                select(Assessment).where(Assessment.id == assignment.assessment_id)
            )
            assessment = assessment_result.scalar_one_or_none()
            events.append(TimelineEvent(
                event_type="assessment",
                event_date=resp.completed_at or resp.started_at or datetime.now(timezone.utc),
                title=f"Assessment: {assessment.title if assessment else 'Unknown'}",
                description="Assessment completed",
                metadata={"response_id": str(resp.id), "assignment_id": str(assignment.id)},
            ))

    # Sort by date descending — normalize tz-naive datetimes to UTC
    def _aware(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    events.sort(key=lambda e: _aware(e.event_date), reverse=True)
    return events
