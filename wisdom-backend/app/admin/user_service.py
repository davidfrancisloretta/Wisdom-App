"""User management service for admin panel."""

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.models import RefreshToken, Role, User
from app.auth.password import hash_password


async def list_users(
    db: AsyncSession,
    role_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    """Return paginated user list with filters."""
    query = select(User).options(selectinload(User.role))
    count_query = select(func.count(User.id))

    if role_filter:
        query = query.join(User.role).where(Role.name == role_filter)
        count_query = count_query.join(User.role).where(Role.name == role_filter)
    if status_filter == "active":
        query = query.where(User.is_active == True)  # noqa: E712
        count_query = count_query.where(User.is_active == True)  # noqa: E712
    elif status_filter == "inactive":
        query = query.where(User.is_active == False)  # noqa: E712
        count_query = count_query.where(User.is_active == False)  # noqa: E712
    if search:
        search_term = f"%{search}%"
        query = query.where(
            (User.full_name.ilike(search_term)) | (User.email.ilike(search_term))
        )
        count_query = count_query.where(
            (User.full_name.ilike(search_term)) | (User.email.ilike(search_term))
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(User.created_at.desc())
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    users = result.scalars().all()

    items = []
    for u in users:
        items.append({
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "phone": u.phone,
            "role": u.role.name if u.role else None,
            "is_active": u.is_active,
            "is_verified": u.is_verified,
            "last_login": u.last_login,
            "created_at": u.created_at,
        })

    return items, total


async def create_user(
    db: AsyncSession,
    email: str,
    password: str,
    full_name: str,
    role_name: str,
    phone: Optional[str] = None,
    is_verified: bool = False,
) -> User:
    """Create a new user with the specified role."""
    # Look up role
    role_result = await db.execute(select(Role).where(Role.name == role_name))
    role = role_result.scalar_one_or_none()
    if not role:
        raise ValueError(f"Role '{role_name}' not found")

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        phone=phone,
        role_id=role.id,
        is_verified=is_verified,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user, ["role"])
    return user


async def update_user(
    db: AsyncSession,
    user_id: UUID,
    **kwargs,
) -> User | None:
    """Update user fields."""
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        return None

    if "role" in kwargs and kwargs["role"]:
        role_result = await db.execute(select(Role).where(Role.name == kwargs.pop("role")))
        role = role_result.scalar_one_or_none()
        if role:
            user.role_id = role.id

    for key, value in kwargs.items():
        if value is not None and hasattr(user, key):
            setattr(user, key, value)

    db.add(user)
    await db.commit()
    await db.refresh(user, ["role"])
    return user


async def deactivate_user(db: AsyncSession, user_id: UUID) -> bool:
    """Deactivate a user account."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return False
    user.is_active = False
    db.add(user)
    await db.commit()
    return True


async def force_logout_user(db: AsyncSession, user_id: UUID) -> None:
    """Revoke all refresh tokens for a user, effectively forcing logout."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked == False)  # noqa: E712
        .values(revoked=True)
    )
    await db.commit()


async def reset_user_password(db: AsyncSession, user_id: UUID, new_password: str) -> bool:
    """Admin reset of a user's password."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return False
    user.hashed_password = hash_password(new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    db.add(user)
    await db.commit()
    return True
