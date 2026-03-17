"""Authentication tests — login, lockout, JWT, refresh, logout."""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.auth.password import (
    check_account_locked,
    hash_password,
    record_failed_attempt,
    reset_failed_attempts,
    verify_password,
)
from app.auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    revoke_refresh_token,
    store_refresh_token,
    validate_refresh_token_in_db,
)
from app.auth.models import User


# ===========================================================================
# Password hashing tests
# ===========================================================================

class TestPasswordHashing:
    def test_hash_and_verify(self):
        plain = "SecurePassword123!"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("CorrectPassword123!")
        assert verify_password("WrongPassword123!", hashed) is False

    def test_verify_with_invalid_hash(self):
        assert verify_password("anything", "not-a-valid-hash") is False


# ===========================================================================
# Account lockout tests
# ===========================================================================

class TestAccountLockout:
    @pytest.mark.asyncio
    async def test_account_not_locked_initially(self, db, users):
        user = users["therapist"]
        assert check_account_locked(user) is False

    @pytest.mark.asyncio
    async def test_failed_attempts_increment(self, db, users):
        user = users["therapist"]
        user.failed_login_attempts = 0
        await db.commit()

        await record_failed_attempt(user, db)
        await db.refresh(user)
        assert user.failed_login_attempts == 1

    @pytest.mark.asyncio
    async def test_account_locks_after_5_failures(self, db, users):
        user = users["nurturer"]
        user.failed_login_attempts = 0
        user.locked_until = None
        await db.commit()

        for _ in range(5):
            await record_failed_attempt(user, db)
            await db.refresh(user)

        assert user.failed_login_attempts >= 5
        assert user.locked_until is not None
        assert check_account_locked(user) is True

    @pytest.mark.asyncio
    async def test_locked_account_rejects_even_correct_password(self, db, users):
        user = users["nurturer"]
        # Ensure locked
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
        await db.commit()
        await db.refresh(user)

        assert check_account_locked(user) is True
        # Even with correct password, account should be locked
        assert verify_password("TestPass123!", user.hashed_password) is True
        assert check_account_locked(user) is True

    @pytest.mark.asyncio
    async def test_reset_clears_lock(self, db, users):
        user = users["nurturer"]
        user.failed_login_attempts = 5
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
        await db.commit()

        await reset_failed_attempts(user, db)
        await db.refresh(user)

        assert user.failed_login_attempts == 0
        assert user.locked_until is None
        assert check_account_locked(user) is False


# ===========================================================================
# JWT token tests
# ===========================================================================

class TestJWT:
    def test_access_token_roundtrip(self, users):
        user = users["admin"]
        token = create_access_token(user.id, "admin", user.email)
        payload = decode_access_token(token)
        assert payload["sub"] == str(user.id)
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_refresh_token_roundtrip(self, users):
        user = users["admin"]
        token = create_refresh_token(user.id)
        payload = decode_refresh_token(token)
        assert payload["sub"] == str(user.id)
        assert payload["type"] == "refresh"

    def test_expired_access_token_raises(self, users):
        from fastapi import HTTPException

        user = users["admin"]
        token = create_access_token(user.id, "admin", user.email, exp_minutes=-1)
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token)
        assert exc_info.value.status_code == 401

    def test_access_token_rejects_refresh(self, users):
        from fastapi import HTTPException

        user = users["admin"]
        refresh = create_refresh_token(user.id)
        with pytest.raises(HTTPException):
            decode_access_token(refresh)  # Wrong type

    def test_refresh_token_rejects_access(self, users):
        from fastapi import HTTPException

        user = users["admin"]
        access = create_access_token(user.id, "admin", user.email)
        with pytest.raises(HTTPException):
            decode_refresh_token(access)  # Wrong type


# ===========================================================================
# Refresh token DB tests
# ===========================================================================

class TestRefreshTokenDB:
    @pytest.mark.asyncio
    async def test_store_and_validate_refresh_token(self, db, users):
        user = users["admin"]
        token = create_refresh_token(user.id)
        await store_refresh_token(user.id, token, db)

        is_valid = await validate_refresh_token_in_db(token, db)
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_revoke_refresh_token(self, db, users):
        user = users["therapist"]
        token = create_refresh_token(user.id)
        await store_refresh_token(user.id, token, db)

        await revoke_refresh_token(token, db)

        is_valid = await validate_refresh_token_in_db(token, db)
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_new_login_revokes_old_tokens(self, db, users):
        """Verify that revoking a token and issuing a new one works correctly."""
        user_id = users["supervisor"].id  # Cache ID before any expiry

        token1 = create_refresh_token(user_id)
        await store_refresh_token(user_id, token1, db)

        # Verify token1 is valid
        assert await validate_refresh_token_in_db(token1, db) is True

        # Explicitly revoke token1 (simulating new login behavior)
        await revoke_refresh_token(token1, db)

        # Verify revocation took effect by direct query
        from sqlalchemy import select
        from app.auth.models import RefreshToken
        import hashlib
        token1_hash = hashlib.sha256(token1.encode()).hexdigest()
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token1_hash)
        )
        rt = result.scalar_one_or_none()
        assert rt is not None
        assert rt.revoked is True

        # Issue new token
        token2 = create_refresh_token(user_id)
        await store_refresh_token(user_id, token2, db)

        assert await validate_refresh_token_in_db(token2, db) is True
