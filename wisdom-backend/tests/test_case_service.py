"""Unit tests for app.cases.service — CRUD, assignments, notes, interventions, milestones, timeline."""

import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cases.models import (
    CaseAssignment,
    CaseNote,
    ChildCase,
    InterventionPlan,
    ProgressMilestone,
)
from app.cases.schemas import (
    CaseCreate,
    CaseUpdate,
    InterventionCreate,
    InterventionUpdate,
    MilestoneCreate,
    NoteCreate,
    NoteUpdate,
)
from app.cases.service import (
    create_assignment,
    create_case,
    create_intervention,
    create_milestone,
    create_note,
    delete_assignment,
    get_case,
    get_timeline,
    list_assignments,
    list_cases,
    list_interventions,
    list_milestones,
    list_notes,
    soft_delete_case,
    update_case,
    update_intervention,
    update_note,
)


# ============================================================================
# Case CRUD
# ============================================================================


class TestCreateCase:
    @pytest.mark.asyncio
    async def test_creates_case_with_case_number(self, db: AsyncSession, users):
        data = CaseCreate(
            first_name="Asha",
            last_name="Rao",
            date_of_birth="2014-03-10",
            gender="female",
            age_at_intake=11,
        )
        result = await create_case(data, users["admin"].id, db)

        assert result["case_number"].startswith("ARK-")
        assert result["status"] == "active"
        assert result["intake_date"] is not None

    @pytest.mark.asyncio
    async def test_sets_default_intake_date(self, db: AsyncSession, users):
        data = CaseCreate(
            first_name="Ravi",
            last_name="Kumar",
            date_of_birth="2013-01-01",
        )
        result = await create_case(data, users["admin"].id, db)
        assert result["intake_date"] == date.today()

    @pytest.mark.asyncio
    async def test_preserves_optional_fields(self, db: AsyncSession, users):
        data = CaseCreate(
            first_name="Priya",
            last_name="Nair",
            date_of_birth="2012-07-20",
            guardian_name="Lakshmi Nair",
            guardian_phone="+91-9876543210",
            school_name="DPS Bengaluru",
            presenting_issues=["anxiety", "social withdrawal"],
        )
        result = await create_case(data, users["admin"].id, db)
        assert result["presenting_issues"] == ["anxiety", "social withdrawal"]

    @pytest.mark.asyncio
    async def test_sequential_case_numbers(self, db: AsyncSession, users):
        data1 = CaseCreate(first_name="A", last_name="B", date_of_birth="2015-01-01")
        data2 = CaseCreate(first_name="C", last_name="D", date_of_birth="2015-02-02")
        r1 = await create_case(data1, users["admin"].id, db)
        r2 = await create_case(data2, users["admin"].id, db)
        # Both should have case numbers but different sequence digits
        assert r1["case_number"] != r2["case_number"]


class TestGetCase:
    @pytest.mark.asyncio
    async def test_get_existing_case(self, db: AsyncSession, child_case):
        result = await get_case(child_case.id, db)
        assert result is not None
        assert result["case_number"] == "ARK-TEST-001"

    @pytest.mark.asyncio
    async def test_get_nonexistent_case(self, db: AsyncSession):
        result = await get_case(uuid.uuid4(), db)
        assert result is None


class TestUpdateCase:
    @pytest.mark.asyncio
    async def test_update_status(self, db: AsyncSession, users):
        data = CaseCreate(first_name="Update", last_name="Test", date_of_birth="2014-01-01")
        case = await create_case(data, users["admin"].id, db)
        updated = await update_case(
            case["id"],
            CaseUpdate(status="on_hold"),
            db,
        )
        assert updated is not None
        assert updated["status"] == "on_hold"

    @pytest.mark.asyncio
    async def test_close_case_sets_closed_date(self, db: AsyncSession, users):
        data = CaseCreate(first_name="Close", last_name="Me", date_of_birth="2014-01-01")
        case = await create_case(data, users["admin"].id, db)
        updated = await update_case(
            case["id"],
            CaseUpdate(status="closed"),
            db,
        )
        assert updated["status"] == "closed"
        assert updated["closed_date"] == date.today()

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_none(self, db: AsyncSession):
        result = await update_case(uuid.uuid4(), CaseUpdate(status="closed"), db)
        assert result is None


class TestSoftDeleteCase:
    @pytest.mark.asyncio
    async def test_soft_delete_sets_closed(self, db: AsyncSession, users):
        data = CaseCreate(first_name="Delete", last_name="Me", date_of_birth="2014-01-01")
        case = await create_case(data, users["admin"].id, db)
        deleted = await soft_delete_case(case["id"], db)
        assert deleted["status"] == "closed"
        assert deleted["closed_date"] is not None

    @pytest.mark.asyncio
    async def test_soft_delete_nonexistent(self, db: AsyncSession):
        result = await soft_delete_case(uuid.uuid4(), db)
        assert result is None


class TestListCases:
    @pytest.mark.asyncio
    async def test_admin_sees_all_cases(self, db: AsyncSession, users, child_case):
        items, total = await list_cases(
            db, user_id=users["admin"].id, user_role="admin"
        )
        assert total >= 1
        assert any(i.case_number == "ARK-TEST-001" for i in items)

    @pytest.mark.asyncio
    async def test_therapist_sees_only_assigned(self, db: AsyncSession, users, child_case, case_assignment):
        items, total = await list_cases(
            db, user_id=users["therapist"].id, user_role="therapist"
        )
        assert total >= 1
        case_ids = [i.id for i in items]
        assert child_case.id in case_ids

    @pytest.mark.asyncio
    async def test_unassigned_therapist_sees_nothing(self, db: AsyncSession, users):
        # nurturer has no assignments (unless created by other tests, use a fresh user)
        items, total = await list_cases(
            db, user_id=users["staff"].id, user_role="staff"
        )
        # staff role is not a bypass role, so filtered by assignments
        # staff has no case assignments
        assert total == 0

    @pytest.mark.asyncio
    async def test_search_by_case_number(self, db: AsyncSession, users, child_case):
        items, total = await list_cases(
            db, user_id=users["admin"].id, user_role="admin", search="ARK-TEST"
        )
        assert total >= 1

    @pytest.mark.asyncio
    async def test_pagination(self, db: AsyncSession, users, child_case):
        items, total = await list_cases(
            db, user_id=users["admin"].id, user_role="admin", page=1, page_size=1
        )
        assert len(items) <= 1


# ============================================================================
# Assignments
# ============================================================================


class TestAssignments:
    @pytest.mark.asyncio
    async def test_create_assignment(self, db: AsyncSession, users, child_case):
        assignment = await create_assignment(
            child_case.id, users["nurturer"].id, "nurturer", users["admin"].id, db
        )
        assert assignment.assignment_type == "nurturer"
        assert assignment.is_active is True

    @pytest.mark.asyncio
    async def test_list_assignments(self, db: AsyncSession, users, child_case, case_assignment):
        items = await list_assignments(child_case.id, db)
        assert len(items) >= 1
        assert any(a.assignment_type == "primary_therapist" for a in items)

    @pytest.mark.asyncio
    async def test_delete_assignment_soft_deletes(self, db: AsyncSession, users, child_case):
        assignment = await create_assignment(
            child_case.id, users["staff"].id, "nurturer", users["admin"].id, db
        )
        result = await delete_assignment(assignment.id, db)
        assert result is True

        # Should no longer appear in active list
        items = await list_assignments(child_case.id, db)
        active_ids = [a.id for a in items]
        assert assignment.id not in active_ids

    @pytest.mark.asyncio
    async def test_delete_nonexistent_assignment(self, db: AsyncSession):
        result = await delete_assignment(uuid.uuid4(), db)
        assert result is False


# ============================================================================
# Notes
# ============================================================================


class TestNotes:
    @pytest.mark.asyncio
    async def test_create_note(self, db: AsyncSession, users, child_case):
        data = NoteCreate(
            note_type="session",
            content="Child showed improvement in social interaction today.",
        )
        note = await create_note(child_case.id, users["therapist"].id, data, db)
        assert note.note_type == "session"
        assert note.case_id == child_case.id

    @pytest.mark.asyncio
    async def test_list_notes_newest_first(self, db: AsyncSession, users, child_case):
        data1 = NoteCreate(note_type="session", content="First note")
        data2 = NoteCreate(note_type="observation", content="Second note")
        await create_note(child_case.id, users["therapist"].id, data1, db)
        await create_note(child_case.id, users["therapist"].id, data2, db)

        items = await list_notes(child_case.id, db)
        assert len(items) >= 2
        # newest first
        assert items[0].created_at >= items[-1].created_at

    @pytest.mark.asyncio
    async def test_list_notes_includes_author_name(self, db: AsyncSession, users, child_case):
        data = NoteCreate(note_type="progress", content="Progress note")
        await create_note(child_case.id, users["therapist"].id, data, db)

        items = await list_notes(child_case.id, db)
        assert items[0].author_name is not None

    @pytest.mark.asyncio
    async def test_update_note_content(self, db: AsyncSession, users, child_case):
        data = NoteCreate(note_type="session", content="Original content")
        note = await create_note(child_case.id, users["therapist"].id, data, db)

        updated = await update_note(note.id, NoteUpdate(content="Updated content"), db)
        assert updated is not None

    @pytest.mark.asyncio
    async def test_update_nonexistent_note(self, db: AsyncSession):
        result = await update_note(uuid.uuid4(), NoteUpdate(content="x"), db)
        assert result is None


# ============================================================================
# Intervention Plans
# ============================================================================


class TestInterventions:
    @pytest.mark.asyncio
    async def test_create_intervention(self, db: AsyncSession, users, child_case):
        data = InterventionCreate(
            goals=[{"text": "Improve social skills"}],
            strategies=[{"text": "Group therapy sessions"}],
            review_date=date(2026, 6, 1),
        )
        plan = await create_intervention(child_case.id, users["therapist"].id, data, db)
        assert plan.status == "active"
        assert len(plan.goals) == 1

    @pytest.mark.asyncio
    async def test_list_interventions(self, db: AsyncSession, users, child_case):
        data = InterventionCreate(
            goals=[{"text": "Test goal"}],
            strategies=[{"text": "Test strategy"}],
        )
        await create_intervention(child_case.id, users["therapist"].id, data, db)
        items = await list_interventions(child_case.id, db)
        assert len(items) >= 1

    @pytest.mark.asyncio
    async def test_update_intervention_status(self, db: AsyncSession, users, child_case):
        data = InterventionCreate(goals=[{"text": "G"}], strategies=[{"text": "S"}])
        plan = await create_intervention(child_case.id, users["therapist"].id, data, db)

        updated = await update_intervention(
            plan.id, InterventionUpdate(status="completed"), db
        )
        assert updated is not None
        assert updated.status == "completed"

    @pytest.mark.asyncio
    async def test_update_nonexistent_intervention(self, db: AsyncSession):
        result = await update_intervention(
            uuid.uuid4(), InterventionUpdate(status="completed"), db
        )
        assert result is None


# ============================================================================
# Milestones
# ============================================================================


class TestMilestones:
    @pytest.mark.asyncio
    async def test_create_milestone(self, db: AsyncSession, users, child_case):
        data = MilestoneCreate(
            milestone_text="First full group session without distress",
            domain="Social Skills",
        )
        ms = await create_milestone(child_case.id, users["therapist"].id, data, db)
        assert ms.milestone_text == "First full group session without distress"
        assert ms.domain == "Social Skills"

    @pytest.mark.asyncio
    async def test_default_milestone_date(self, db: AsyncSession, users, child_case):
        data = MilestoneCreate(milestone_text="Default date test")
        ms = await create_milestone(child_case.id, users["therapist"].id, data, db)
        assert ms.milestone_date == date.today()

    @pytest.mark.asyncio
    async def test_list_milestones(self, db: AsyncSession, users, child_case):
        data = MilestoneCreate(milestone_text="Listed milestone", domain="Behaviour")
        await create_milestone(child_case.id, users["therapist"].id, data, db)
        items = await list_milestones(child_case.id, db)
        assert len(items) >= 1


# ============================================================================
# Timeline
# ============================================================================


class TestTimeline:
    @pytest.mark.asyncio
    async def test_timeline_includes_notes(self, db: AsyncSession, users, child_case):
        note_data = NoteCreate(note_type="session", content="Timeline note")
        await create_note(child_case.id, users["therapist"].id, note_data, db)

        events = await get_timeline(child_case.id, db)
        note_events = [e for e in events if e.event_type == "note"]
        assert len(note_events) >= 1

    @pytest.mark.asyncio
    async def test_timeline_includes_milestones(self, db: AsyncSession, users, child_case):
        ms_data = MilestoneCreate(milestone_text="Timeline milestone")
        await create_milestone(child_case.id, users["therapist"].id, ms_data, db)

        events = await get_timeline(child_case.id, db)
        ms_events = [e for e in events if e.event_type == "milestone"]
        assert len(ms_events) >= 1

    @pytest.mark.asyncio
    async def test_timeline_sorted_newest_first(self, db: AsyncSession, users, child_case):
        events = await get_timeline(child_case.id, db)
        if len(events) >= 2:
            def _aware(dt: datetime) -> datetime:
                return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
            assert _aware(events[0].event_date) >= _aware(events[-1].event_date)

    @pytest.mark.asyncio
    async def test_empty_timeline(self, db: AsyncSession, users):
        # Create a fresh case with no activity
        data = CaseCreate(first_name="Empty", last_name="Timeline", date_of_birth="2014-01-01")
        case = await create_case(data, users["admin"].id, db)
        events = await get_timeline(case["id"], db)
        assert events == []
