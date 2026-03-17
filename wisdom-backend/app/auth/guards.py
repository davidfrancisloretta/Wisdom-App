"""FastAPI dependency guards for authentication and authorization."""

from functools import wraps
from typing import Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.abac import check_case_access, check_note_access
from app.auth.jwt import decode_access_token
from app.auth.models import User
from app.database import get_db


# ---------------------------------------------------------------------------
# get_current_user — decodes JWT from cookie or Authorization header
# ---------------------------------------------------------------------------

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate the access token, returning the full User object."""
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(token)
    user_id = UUID(payload["sub"])

    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    return user


# ---------------------------------------------------------------------------
# Role-based guards
# ---------------------------------------------------------------------------

def require_role(*roles: str) -> Callable:
    """Return a dependency that raises 403 if user's role is not in the allowed set."""
    async def _guard(user: User = Depends(get_current_user)) -> User:
        role_name = user.role.name if user.role else None
        if role_name not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{role_name}' does not have access to this resource",
            )
        return user
    return _guard


# Convenience shortcuts
require_admin = require_role("super_admin", "admin")
require_clinical = require_role("super_admin", "admin", "chief_therapist", "supervisor", "therapist")
require_super_admin = require_role("super_admin")


# ---------------------------------------------------------------------------
# ABAC guards
# ---------------------------------------------------------------------------

async def require_case_access(
    case_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Verify the user has ABAC access to the specified case."""
    has_access = await check_case_access(user.id, case_id, db)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this case",
        )
    return user


async def require_note_access(
    note_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Verify the user has ABAC access to the specified note."""
    has_access = await check_note_access(user.id, note_id, db)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this note",
        )
    return user
