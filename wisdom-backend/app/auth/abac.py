"""ABAC (Attribute-Based Access Control) enforcement layer.

ABAC runs after RBAC passes. It checks whether a specific user can access a
specific record instance based on assignments, authorship, and team scope.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.models import User, UserAttribute
from app.cases.models import CaseAssignment, CaseNote

# Roles that bypass ABAC checks entirely
BYPASS_ROLES = {"super_admin", "admin", "chief_therapist"}


async def _get_user_role(user_id: UUID, db: AsyncSession) -> str | None:
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user or not user.role:
        return None
    return user.role.name


async def check_case_access(user_id: UUID, case_id: UUID, db: AsyncSession) -> bool:
    """
    Returns True if:
    - User role is super_admin, admin, or chief_therapist (bypass)
    - OR user has an active CaseAssignment record for this case
    """
    role = await _get_user_role(user_id, db)
    if role in BYPASS_ROLES:
        return True

    result = await db.execute(
        select(CaseAssignment).where(
            CaseAssignment.case_id == case_id,
            CaseAssignment.user_id == user_id,
            CaseAssignment.is_active == True,  # noqa: E712
        )
    )
    return result.scalar_one_or_none() is not None


async def check_note_access(user_id: UUID, note_id: UUID, db: AsyncSession) -> bool:
    """
    Returns True if:
    - User role is super_admin, admin, or chief_therapist (bypass)
    - OR user is the note author
    - OR user is the supervisor of the case the note belongs to
    """
    role = await _get_user_role(user_id, db)
    if role in BYPASS_ROLES:
        return True

    result = await db.execute(
        select(CaseNote).where(CaseNote.id == note_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        return False

    # Author can always access their own notes
    if note.author_id == user_id:
        return True

    # Supervisor of the case can access notes
    supervisor_result = await db.execute(
        select(CaseAssignment).where(
            CaseAssignment.case_id == note.case_id,
            CaseAssignment.user_id == user_id,
            CaseAssignment.assignment_type == "supervisor",
            CaseAssignment.is_active == True,  # noqa: E712
        )
    )
    return supervisor_result.scalar_one_or_none() is not None


async def check_supervisor_scope(user_id: UUID, case_id: UUID, db: AsyncSession) -> bool:
    """
    Returns True if user has a supervisor CaseAssignment
    for any case in the same team as the target case.

    Team is determined by shared assignment — if the supervisor oversees
    any case that shares an assigned therapist/nurturer with the target case,
    they have scope.
    """
    role = await _get_user_role(user_id, db)
    if role in BYPASS_ROLES:
        return True

    # Get all cases this supervisor is assigned to
    sup_cases = await db.execute(
        select(CaseAssignment.case_id).where(
            CaseAssignment.user_id == user_id,
            CaseAssignment.assignment_type == "supervisor",
            CaseAssignment.is_active == True,  # noqa: E712
        )
    )
    supervised_case_ids = {row[0] for row in sup_cases.all()}

    # Direct supervision
    if case_id in supervised_case_ids:
        return True

    # Check if target case shares a team member with any supervised case
    if supervised_case_ids:
        # Get all staff assigned to supervised cases
        team_members = await db.execute(
            select(CaseAssignment.user_id).where(
                CaseAssignment.case_id.in_(supervised_case_ids),
                CaseAssignment.is_active == True,  # noqa: E712
            )
        )
        team_member_ids = {row[0] for row in team_members.all()}

        # Check if any team member is assigned to the target case
        target_assignments = await db.execute(
            select(CaseAssignment).where(
                CaseAssignment.case_id == case_id,
                CaseAssignment.user_id.in_(team_member_ids),
                CaseAssignment.is_active == True,  # noqa: E712
            )
        )
        return target_assignments.scalar_one_or_none() is not None

    return False


async def check_parent_case_access(
    parent_user_id: UUID, case_id: UUID, db: AsyncSession
) -> bool:
    """
    Returns True if the parent user's linked child_case_id matches.
    Parent-case linkage is stored in UserAttribute with key 'child_case_id'.
    """
    result = await db.execute(
        select(UserAttribute).where(
            UserAttribute.user_id == parent_user_id,
            UserAttribute.attribute_key == "child_case_id",
            UserAttribute.attribute_value == str(case_id),
        )
    )
    return result.scalar_one_or_none() is not None
