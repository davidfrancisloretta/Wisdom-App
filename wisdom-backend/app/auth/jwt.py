"""JWT token creation, validation, and cookie handling."""

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, Response
from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import RefreshToken
from app.config import get_settings

settings = get_settings()

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: UUID,
    role: str,
    email: str,
    exp_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "email": email,
        "type": "access",
        "exp": now + timedelta(minutes=exp_minutes),
        "iat": now,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(
    user_id: UUID,
    exp_days: int = REFRESH_TOKEN_EXPIRE_DAYS,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": now + timedelta(days=exp_days),
        "iat": now,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------

def decode_access_token(token: str) -> dict:
    """Decode and validate an access JWT. Raises 401 on failure."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired access token")


def decode_refresh_token(token: str) -> dict:
    """Decode and validate a refresh JWT. Raises 401 on failure."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


# ---------------------------------------------------------------------------
# Refresh-token DB helpers
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def store_refresh_token(
    user_id: UUID, token: str, db: AsyncSession
) -> None:
    """Revoke existing refresh tokens for the user, then store the new one."""
    # Revoke all prior tokens for this user
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked == False)  # noqa: E712
        .values(revoked=True)
    )
    new_rt = RefreshToken(
        user_id=user_id,
        token_hash=_hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(new_rt)
    await db.commit()


async def validate_refresh_token_in_db(token: str, db: AsyncSession) -> bool:
    """Check that a refresh token exists in DB and is not revoked."""
    token_hash = _hash_token(token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked == False,  # noqa: E712
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    return result.scalar_one_or_none() is not None


async def revoke_refresh_token(token: str, db: AsyncSession) -> None:
    """Mark a specific refresh token as revoked."""
    token_hash = _hash_token(token)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .values(revoked=True)
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Set httpOnly cookies for access and refresh tokens."""
    is_prod = settings.ENVIRONMENT == "production"

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=is_prod,
        samesite="strict",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/v1/auth",
    )


def clear_auth_cookies(response: Response) -> None:
    """Remove auth cookies."""
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/api/v1/auth")
