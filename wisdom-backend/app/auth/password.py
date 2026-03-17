"""Password hashing and account lockout utilities using Argon2."""

from datetime import datetime, timedelta, timezone

from passlib.hash import argon2
from sqlalchemy.ext.asyncio import AsyncSession


def hash_password(plain: str) -> str:
    """Hash a plaintext password using Argon2."""
    return argon2.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against an Argon2 hash."""
    try:
        return argon2.verify(plain, hashed)
    except Exception:
        return False


def check_account_locked(user) -> bool:
    """Return True if the user account is currently locked."""
    if user.locked_until is None:
        return False
    now = datetime.now(timezone.utc)
    if user.locked_until.tzinfo is None:
        locked = user.locked_until.replace(tzinfo=timezone.utc)
    else:
        locked = user.locked_until
    return locked > now


async def record_failed_attempt(user, db: AsyncSession) -> None:
    """Increment failed login counter; lock account after 5 failures for 30 minutes."""
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= 5:
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
    db.add(user)
    await db.commit()


async def reset_failed_attempts(user, db: AsyncSession) -> None:
    """Clear failed login counter and lock on successful login."""
    user.failed_login_attempts = 0
    user.locked_until = None
    db.add(user)
    await db.commit()
