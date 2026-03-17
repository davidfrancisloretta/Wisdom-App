"""Tests for AI features — mocked LiteLLM responses."""

import json
from datetime import date
from uuid import uuid4

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TestSessionLocal, test_engine
from app.cases.models import CaseNote, ChildCase


# ---------------------------------------------------------------------------
# Helper: fake LiteLLM response
# ---------------------------------------------------------------------------

def _mock_litellm_response(content: str):
    """Create a mock object that mimics a LiteLLM completion response."""
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = content
    return mock


# ---------------------------------------------------------------------------
# 1. summarise_case_notes returns a summary when notes exist
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summarise_case_notes_returns_summary(db: AsyncSession, child_case: ChildCase, users):
    """When notes exist for a case, summarise_case_notes should return the LLM summary."""
    # Create a case note (plaintext — no encryption needed in test DB)
    note = CaseNote(
        case_id=child_case.id,
        author_id=users["therapist"].id,
        note_type="session",
        content="Child showed improved emotional regulation during play therapy today.",
        session_date=date(2025, 6, 1),
    )
    db.add(note)
    await db.commit()

    expected_summary = "The child is making progress in emotional regulation through play therapy."

    with (
        patch("app.ai.summariser.litellm") as mock_litellm,
        patch("app.security.encryption.decrypt_field", side_effect=lambda x: x),
        patch("app.ai.summariser.redis_client") as mock_redis,
        patch("app.ai.summariser.get_settings") as mock_settings,
    ):
        mock_litellm.acompletion = AsyncMock(
            return_value=_mock_litellm_response(expected_summary)
        )
        mock_redis.get = AsyncMock(return_value=None)  # no cache hit
        mock_redis.set = AsyncMock()
        mock_settings.return_value = MagicMock(
            LITELLM_MODEL="gpt-4o", LITELLM_API_KEY="test-key"
        )

        from app.ai.summariser import summarise_case_notes

        result = await summarise_case_notes(child_case.id, db)

    assert result
    assert len(result) > 0
    assert result == expected_summary


# ---------------------------------------------------------------------------
# 2. summarise_case_notes returns message when no notes exist
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summarise_case_notes_empty_notes(db: AsyncSession, users):
    """When a case has no notes, return the 'no notes found' message."""
    # Create a case with no notes
    empty_case = ChildCase(
        case_number=f"ARK-EMPTY-{uuid4().hex[:6].upper()}",
        first_name="Empty",
        last_name="Case",
        date_of_birth="2014-01-01",
        status="active",
        created_by=users["admin"].id,
    )
    db.add(empty_case)
    await db.commit()
    await db.refresh(empty_case)

    with patch("app.ai.summariser.redis_client") as mock_redis:
        mock_redis.get = AsyncMock(return_value=None)

        from app.ai.summariser import summarise_case_notes

        result = await summarise_case_notes(empty_case.id, db)

    assert result == "No session notes found for this case."


# ---------------------------------------------------------------------------
# 3. detect_behavioural_risks returns a JSON array of dicts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_risks_returns_json_array(db: AsyncSession, child_case: ChildCase, users):
    """detect_behavioural_risks should return a list of risk dicts from the LLM."""
    risk_json = json.dumps([
        {
            "type": "withdrawal",
            "severity": "medium",
            "evidence": "Child has shown increased isolation in last two sessions.",
            "recommendation": "Monitor peer interactions and consider group therapy.",
        }
    ])

    with (
        patch("app.ai.risk_detector.litellm") as mock_litellm,
        patch("app.security.encryption.decrypt_field", side_effect=lambda x: x),
        patch("app.ai.risk_detector.get_settings") as mock_settings,
    ):
        mock_litellm.acompletion = AsyncMock(
            return_value=_mock_litellm_response(risk_json)
        )
        mock_settings.return_value = MagicMock(
            LITELLM_MODEL="gpt-4o", LITELLM_API_KEY="test-key"
        )

        from app.ai.risk_detector import detect_behavioural_risks

        result = await detect_behavioural_risks(child_case.id, db)

    assert isinstance(result, list)
    assert len(result) >= 1
    first = result[0]
    assert isinstance(first, dict)
    assert "type" in first
    assert "severity" in first
    assert "evidence" in first
    assert "recommendation" in first
    assert first["type"] == "withdrawal"
    assert first["severity"] == "medium"


# ---------------------------------------------------------------------------
# 4. LiteLLM failure returns graceful fallback (no crash)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_litellm_failure_returns_graceful_fallback(db: AsyncSession, child_case: ChildCase, users):
    """When LiteLLM raises an exception, the summariser should return a fallback message."""
    # Ensure there is at least one note so the function reaches the LLM call
    note = CaseNote(
        case_id=child_case.id,
        author_id=users["therapist"].id,
        note_type="session",
        content="Follow-up session — child was quiet but cooperative.",
        session_date=date(2025, 7, 1),
    )
    db.add(note)
    await db.commit()

    with (
        patch("app.ai.summariser.litellm") as mock_litellm,
        patch("app.security.encryption.decrypt_field", side_effect=lambda x: x),
        patch("app.ai.summariser.redis_client") as mock_redis,
        patch("app.ai.summariser.get_settings") as mock_settings,
    ):
        mock_litellm.acompletion = AsyncMock(
            side_effect=Exception("LiteLLM service unavailable")
        )
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        mock_settings.return_value = MagicMock(
            LITELLM_MODEL="gpt-4o", LITELLM_API_KEY="test-key"
        )

        from app.ai.summariser import summarise_case_notes

        result = await summarise_case_notes(child_case.id, db)

    assert "AI summary temporarily unavailable" in result


# ---------------------------------------------------------------------------
# 5. Rate limiter returns 429-equivalent after 10 calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_endpoint_rate_limit():
    """
    The check_rate_limit helper should return False (over limit) when
    the Redis counter exceeds 10.
    """
    from app.ai.router import check_rate_limit

    user_id = uuid4()

    with patch("app.ai.router.redis_client") as mock_redis:
        # Simulate the 11th call — incr returns 11
        mock_redis.incr = AsyncMock(return_value=11)
        mock_redis.expire = AsyncMock()

        allowed = await check_rate_limit(user_id)

    assert allowed is False, "The 11th call in a minute should be rate-limited"


@pytest.mark.asyncio
async def test_ai_endpoint_within_rate_limit():
    """
    The check_rate_limit helper should return True when under the limit.
    """
    from app.ai.router import check_rate_limit

    user_id = uuid4()

    with patch("app.ai.router.redis_client") as mock_redis:
        # Simulate the 5th call — well within the limit
        mock_redis.incr = AsyncMock(return_value=5)
        mock_redis.expire = AsyncMock()

        allowed = await check_rate_limit(user_id)

    assert allowed is True, "Calls within the 10/min limit should be allowed"
