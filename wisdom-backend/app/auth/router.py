"""Authentication routes — login, logout, refresh, password management."""

import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import (
    clear_auth_cookies,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    revoke_refresh_token,
    set_auth_cookies,
    store_refresh_token,
    validate_refresh_token_in_db,
)
from app.auth.models import Role, User
from app.auth.password import (
    check_account_locked,
    hash_password,
    record_failed_attempt,
    reset_failed_attempts,
    verify_password,
)
from app.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ParentLoginRequest,
    ResetPasswordRequest,
    UserProfile,
)
from app.database import get_db

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_user_by_email(email: str, db: AsyncSession) -> User | None:
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.email == email)
    )
    return result.scalar_one_or_none()


async def _log_audit(
    db: AsyncSession,
    user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    request: Request,
) -> None:
    """Write an audit log entry (inline import to avoid circular deps)."""
    from app.cases.models import AuditLog

    entry = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(entry)
    await db.commit()


# ---------------------------------------------------------------------------
# POST /login — staff + admin login
# ---------------------------------------------------------------------------

@router.post("/login", response_model=UserProfile)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user_by_email(body.email, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Must not be a parent
    if user.role and user.role.name == "parent":
        raise HTTPException(status_code=401, detail="Please use the parent login")

    # Account active & verified
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Account not yet verified by admin")

    # Account lockout
    if check_account_locked(user):
        raise HTTPException(status_code=423, detail="Account is locked. Try again later.")

    # Password check
    if not verify_password(body.password, user.hashed_password):
        await record_failed_attempt(user, db)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Success
    await reset_failed_attempts(user, db)
    user.last_login = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()

    role_name = user.role.name if user.role else "staff"
    access = create_access_token(user.id, role_name, user.email)
    refresh = create_refresh_token(user.id)
    await store_refresh_token(user.id, refresh, db)
    set_auth_cookies(response, access, refresh)

    await _log_audit(db, user.id, "LOGIN", "User", str(user.id), request)

    return UserProfile(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        role=role_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        last_login=user.last_login,
    )


# ---------------------------------------------------------------------------
# POST /parent-login — parent/caretaker login
# ---------------------------------------------------------------------------

@router.post("/parent-login", response_model=UserProfile)
async def parent_login(
    body: ParentLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user_by_email(body.email, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Must be a parent
    if not user.role or user.role.name != "parent":
        raise HTTPException(status_code=401, detail="Please use the staff login")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Account not yet verified")

    if check_account_locked(user):
        raise HTTPException(status_code=423, detail="Account is locked. Try again later.")

    if not verify_password(body.password, user.hashed_password):
        await record_failed_attempt(user, db)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await reset_failed_attempts(user, db)
    user.last_login = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()

    access = create_access_token(user.id, "parent", user.email)
    refresh = create_refresh_token(user.id)
    await store_refresh_token(user.id, refresh, db)
    set_auth_cookies(response, access, refresh)

    await _log_audit(db, user.id, "LOGIN", "User", str(user.id), request)

    return UserProfile(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        role="parent",
        is_active=user.is_active,
        is_verified=user.is_verified,
        last_login=user.last_login,
    )


# ---------------------------------------------------------------------------
# POST /refresh — refresh access token
# ---------------------------------------------------------------------------

@router.post("/refresh", response_model=UserProfile)
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")

    payload = decode_refresh_token(refresh_token)

    # Verify token exists in DB and is not revoked
    if not await validate_refresh_token_in_db(refresh_token, db):
        raise HTTPException(status_code=401, detail="Refresh token revoked or expired")

    user_id = UUID(payload["sub"])
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Revoke old, issue new
    await revoke_refresh_token(refresh_token, db)
    role_name = user.role.name if user.role else "staff"
    new_access = create_access_token(user.id, role_name, user.email)
    new_refresh = create_refresh_token(user.id)
    await store_refresh_token(user.id, new_refresh, db)
    set_auth_cookies(response, new_access, new_refresh)

    return UserProfile(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        role=role_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        last_login=user.last_login,
    )


# ---------------------------------------------------------------------------
# POST /logout — revoke refresh token, clear cookies
# ---------------------------------------------------------------------------

@router.post("/logout", response_model=MessageResponse)
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    if refresh_token:
        await revoke_refresh_token(refresh_token, db)
    clear_auth_cookies(response)
    return MessageResponse(message="Logged out successfully")


# ---------------------------------------------------------------------------
# GET /me — current user profile and permissions
# ---------------------------------------------------------------------------

@router.get("/me", response_model=UserProfile)
async def me(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
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

    role_name = user.role.name if user.role else "staff"
    return UserProfile(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        role=role_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        last_login=user.last_login,
    )


# ---------------------------------------------------------------------------
# POST /change-password
# ---------------------------------------------------------------------------

@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(token)
    user_id = UUID(payload["sub"])
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.hashed_password = hash_password(body.new_password)
    db.add(user)
    await db.commit()

    await _log_audit(db, user.id, "CHANGE_PASSWORD", "User", str(user.id), request)
    return MessageResponse(message="Password changed successfully")


# ---------------------------------------------------------------------------
# POST /forgot-password
# ---------------------------------------------------------------------------

@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    # Always return success to prevent email enumeration
    user = await _get_user_by_email(body.email, db)
    if user:
        # Generate a reset token and store it (in production: send via email/WhatsApp)
        reset_token = secrets.token_urlsafe(32)
        # Store in user_attributes for later verification
        from app.auth.models import UserAttribute

        # Remove old reset tokens
        result = await db.execute(
            select(UserAttribute).where(
                UserAttribute.user_id == user.id,
                UserAttribute.attribute_key == "password_reset_token",
            )
        )
        old_token = result.scalar_one_or_none()
        if old_token:
            await db.delete(old_token)

        attr = UserAttribute(
            user_id=user.id,
            attribute_key="password_reset_token",
            attribute_value=reset_token,
        )
        db.add(attr)
        await db.commit()
        # TODO: Send reset link via email/WhatsApp in production

    return MessageResponse(message="If an account exists, a reset link has been sent")


# ---------------------------------------------------------------------------
# POST /reset-password/{token}
# ---------------------------------------------------------------------------

@router.post("/reset-password/{token}", response_model=MessageResponse)
async def reset_password(
    token: str,
    body: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from app.auth.models import UserAttribute

    result = await db.execute(
        select(UserAttribute).where(
            UserAttribute.attribute_key == "password_reset_token",
            UserAttribute.attribute_value == token,
        )
    )
    attr = result.scalar_one_or_none()
    if not attr:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user_result = await db.execute(select(User).where(User.id == attr.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.hashed_password = hash_password(body.new_password)
    db.add(user)
    await db.delete(attr)
    await db.commit()

    await _log_audit(db, user.id, "RESET_PASSWORD", "User", str(user.id), request)
    return MessageResponse(message="Password reset successfully")
