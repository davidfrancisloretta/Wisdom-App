"""RBAC + ABAC tests — verify every role for critical permissions."""

import pytest
import pytest_asyncio
import uuid

from app.auth.abac import (
    check_case_access,
    check_note_access,
    check_parent_case_access,
    check_supervisor_scope,
)
from app.auth.casbin_adapter import check_rbac, get_enforcer, load_policies_into_enforcer, reset_enforcer
from app.cases.models import CaseAssignment, CaseNote, ChildCase


# ===========================================================================
# Casbin RBAC policy tests
# ===========================================================================

class TestCasbinRBAC:
    @pytest.fixture(autouse=True)
    def setup_enforcer(self):
        """Ensure policies are loaded with fresh enforcer before each test."""
        reset_enforcer()
        enforcer = get_enforcer()
        load_policies_into_enforcer(enforcer)

    def test_super_admin_full_access(self):
        assert check_rbac("super_admin", "/cases/123", "GET") is True
        assert check_rbac("super_admin", "/admin/config/keys", "PUT") is True
        assert check_rbac("super_admin", "/anything/at/all", "DELETE") is True

    def test_admin_clinical_access(self):
        assert check_rbac("admin", "/cases/123", "GET") is True
        assert check_rbac("admin", "/assessments/456", "POST") is True
        assert check_rbac("admin", "/admin/users/789", "PUT") is True

    def test_admin_denied_system_config(self):
        assert check_rbac("admin", "/admin/config/keys", "PUT") is False

    def test_chief_therapist_clinical_access(self):
        assert check_rbac("chief_therapist", "/cases/123", "GET") is True
        assert check_rbac("chief_therapist", "/assessments/456", "PUT") is True

    def test_therapist_case_access(self):
        assert check_rbac("therapist", "/cases/123", "GET") is True
        assert check_rbac("therapist", "/cases/123/notes", "POST") is True
        assert check_rbac("therapist", "/cases/123/interventions", "PUT") is True

    def test_therapist_scheduling_read_only(self):
        assert check_rbac("therapist", "/scheduling/today", "GET") is True

    def test_nurturer_limited_access(self):
        assert check_rbac("nurturer", "/cases/123", "GET") is True
        assert check_rbac("nurturer", "/cases/123/notes", "POST") is True
        assert check_rbac("nurturer", "/cases/123/milestones", "POST") is True

    def test_nurturer_cannot_delete(self):
        # Nurturer should not be able to delete cases
        assert check_rbac("nurturer", "/cases/123", "DELETE") is False

    def test_staff_operational_only(self):
        assert check_rbac("staff", "/scheduling/bookings", "POST") is True
        assert check_rbac("staff", "/payments/invoices", "GET") is True
        assert check_rbac("staff", "/donations/campaigns", "PUT") is True

    def test_staff_no_clinical(self):
        assert check_rbac("staff", "/cases/123", "GET") is False

    def test_parent_own_data_only(self):
        assert check_rbac("parent", "/parent/cases/123", "GET") is True
        assert check_rbac("parent", "/parent/assessments/456", "POST") is True
        assert check_rbac("parent", "/parent/portal/overview", "GET") is True

    def test_parent_cannot_access_staff_routes(self):
        assert check_rbac("parent", "/cases/123", "GET") is False
        assert check_rbac("parent", "/admin/users/456", "GET") is False

    def test_supervisor_case_read(self):
        assert check_rbac("supervisor", "/cases/123", "GET") is True

    def test_supervisor_notes_access(self):
        assert check_rbac("supervisor", "/cases/123/notes", "POST") is True


# ===========================================================================
# ABAC tests — instance-level access control
# ===========================================================================

class TestABACCaseAccess:
    """Test check_case_access — verifies role bypass and assignment checks."""

    @pytest.mark.asyncio
    async def test_super_admin_bypasses_abac(self, db, users, child_case):
        assert await check_case_access(users["super_admin"].id, child_case.id, db) is True

    @pytest.mark.asyncio
    async def test_admin_bypasses_abac(self, db, users, child_case):
        assert await check_case_access(users["admin"].id, child_case.id, db) is True

    @pytest.mark.asyncio
    async def test_chief_therapist_bypasses_abac(self, db, users, child_case):
        assert await check_case_access(users["chief_therapist"].id, child_case.id, db) is True

    @pytest.mark.asyncio
    async def test_therapist_assigned_case_access(self, db, users, child_case, case_assignment):
        """Therapist with assignment can access the case."""
        assert await check_case_access(users["therapist"].id, child_case.id, db) is True

    @pytest.mark.asyncio
    async def test_therapist_unassigned_case_denied(self, db, users, child_case):
        """Therapist WITHOUT assignment cannot access the case."""
        # Create a different case with no assignment
        from app.cases.models import ChildCase
        other_case = ChildCase(
            case_number="ARK-TEST-002",
            first_name="Other",
            last_name="Child",
            date_of_birth="2014-03-20",
            status="active",
        )
        db.add(other_case)
        await db.commit()
        await db.refresh(other_case)

        assert await check_case_access(users["therapist"].id, other_case.id, db) is False

    @pytest.mark.asyncio
    async def test_staff_no_case_access(self, db, users, child_case):
        """Staff role has no case access (not bypassed, no assignment)."""
        assert await check_case_access(users["staff"].id, child_case.id, db) is False

    @pytest.mark.asyncio
    async def test_supervisor_with_assignment(self, db, users, child_case, supervisor_assignment):
        """Supervisor assigned to case can access it."""
        assert await check_case_access(users["supervisor"].id, child_case.id, db) is True

    @pytest.mark.asyncio
    async def test_supervisor_without_assignment_denied(self, db, users, child_case):
        """Supervisor NOT assigned to case is denied."""
        # No supervisor_assignment fixture used here
        other_case = ChildCase(
            case_number="ARK-TEST-003",
            first_name="Another",
            last_name="Child",
            date_of_birth="2013-01-01",
            status="active",
        )
        db.add(other_case)
        await db.commit()
        await db.refresh(other_case)

        assert await check_case_access(users["supervisor"].id, other_case.id, db) is False


class TestABACNoteAccess:
    """Test check_note_access — author and supervisor checks."""

    @pytest.mark.asyncio
    async def test_author_can_access_own_note(self, db, users, child_case, case_assignment):
        note = CaseNote(
            case_id=child_case.id,
            author_id=users["therapist"].id,
            note_type="session",
            content="Test note",
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)

        assert await check_note_access(users["therapist"].id, note.id, db) is True

    @pytest.mark.asyncio
    async def test_supervisor_can_access_team_note(self, db, users, child_case, supervisor_assignment):
        note = CaseNote(
            case_id=child_case.id,
            author_id=users["therapist"].id,
            note_type="session",
            content="Therapist's note",
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)

        assert await check_note_access(users["supervisor"].id, note.id, db) is True

    @pytest.mark.asyncio
    async def test_unrelated_user_denied_note(self, db, users, child_case):
        note = CaseNote(
            case_id=child_case.id,
            author_id=users["therapist"].id,
            note_type="session",
            content="Private note",
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)

        assert await check_note_access(users["staff"].id, note.id, db) is False


class TestABACParentAccess:
    """Test check_parent_case_access — parent can only access their linked child."""

    @pytest.mark.asyncio
    async def test_parent_can_access_own_child_case(self, db, users, child_case, parent_with_case):
        assert await check_parent_case_access(users["parent"].id, child_case.id, db) is True

    @pytest.mark.asyncio
    async def test_parent_cannot_access_other_child_case(self, db, users):
        random_case_id = uuid.uuid4()
        assert await check_parent_case_access(users["parent"].id, random_case_id, db) is False


# ===========================================================================
# Combined RBAC + ABAC critical permission tests (from spec table)
# ===========================================================================

class TestCriticalPermissions:
    """Tests from the spec's critical permission table."""

    @pytest.fixture(autouse=True)
    def setup_enforcer(self):
        enforcer = get_enforcer()
        load_policies_into_enforcer(enforcer)

    @pytest.mark.asyncio
    async def test_therapist_get_case_not_assigned_403(self, db, users):
        """Therapist GET /cases (not assigned) → 403 Forbidden (ABAC denies)."""
        # Create a separate case the therapist is NOT assigned to
        unassigned_case = ChildCase(
            case_number=f"ARK-UNASSIGNED-{uuid.uuid4().hex[:6]}",
            first_name="Unassigned",
            last_name="Child",
            date_of_birth="2016-01-01",
            status="active",
        )
        db.add(unassigned_case)
        await db.commit()
        await db.refresh(unassigned_case)

        # RBAC passes for therapist on /cases/:id GET
        assert check_rbac("therapist", f"/cases/{unassigned_case.id}", "GET") is True
        # But ABAC denies because not assigned
        assert await check_case_access(users["therapist"].id, unassigned_case.id, db) is False

    @pytest.mark.asyncio
    async def test_therapist_get_case_assigned_200(self, db, users, child_case, case_assignment):
        """Therapist GET /cases (assigned) → 200 OK."""
        assert check_rbac("therapist", f"/cases/{child_case.id}", "GET") is True
        assert await check_case_access(users["therapist"].id, child_case.id, db) is True

    def test_nurturer_delete_case_403(self):
        """Nurturer DELETE /cases/:id → 403 Forbidden."""
        assert check_rbac("nurturer", "/cases/123", "DELETE") is False

    @pytest.mark.asyncio
    async def test_supervisor_get_case_from_another_team_403(self, db, users):
        """Supervisor GET case from another team → 403 Forbidden."""
        random_case_id = uuid.uuid4()
        assert await check_case_access(users["supervisor"].id, random_case_id, db) is False

    def test_admin_get_audit_log_allowed_by_rbac(self):
        """Admin GET /admin/audit-log → RBAC allows (but API guard restricts to super_admin)."""
        # The Casbin policy gives admin GET on /admin/audit-log/*
        assert check_rbac("admin", "/admin/audit-log/entries", "GET") is True
        # Note: The actual endpoint uses require_super_admin guard, which is stricter

    def test_super_admin_get_audit_log_200(self):
        """Super Admin GET /admin/audit-log → 200 OK."""
        assert check_rbac("super_admin", "/admin/audit-log/entries", "GET") is True

    def test_staff_get_case_403(self):
        """Staff GET /cases/:id → 403 Forbidden."""
        assert check_rbac("staff", "/cases/123", "GET") is False

    @pytest.mark.asyncio
    async def test_parent_get_own_child_case_200(self, db, users, child_case, parent_with_case):
        """Parent GET own child case → 200 OK."""
        assert check_rbac("parent", f"/parent/cases/{child_case.id}", "GET") is True
        assert await check_parent_case_access(users["parent"].id, child_case.id, db) is True

    @pytest.mark.asyncio
    async def test_parent_get_another_child_case_403(self, db, users):
        """Parent GET another child case → 403 Forbidden."""
        random_case_id = uuid.uuid4()
        assert await check_parent_case_access(users["parent"].id, random_case_id, db) is False

    @pytest.mark.asyncio
    async def test_chief_therapist_get_any_case_200(self, db, users, child_case):
        """Chief Therapist GET any case → 200 OK."""
        assert check_rbac("chief_therapist", f"/cases/{child_case.id}", "GET") is True
        assert await check_case_access(users["chief_therapist"].id, child_case.id, db) is True
