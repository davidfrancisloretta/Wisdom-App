"""Tests for public endpoints — accessibility and safety.

All public endpoints must return 200 without authentication.
No Bearer token, no login cookie — completely open access.
"""

import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.conftest import TestSessionLocal, test_engine
from app.database import Base, get_db


# ---------------------------------------------------------------------------
# Test app factory — mount ONLY the public router
# ---------------------------------------------------------------------------

def _create_public_app():
    from fastapi import FastAPI
    from app.public.router import router as public_router

    # Ensure public model tables exist (import registers them with Base.metadata)
    import app.public.models  # noqa: F401

    app = FastAPI()
    app.include_router(public_router, prefix="/api/v1/public")

    async def _override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    return app


@pytest_asyncio.fixture(scope="module")
async def setup_public_tables():
    """Create all tables needed by the public module."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Tables are cleaned up by conftest's session-scoped teardown


@pytest_asyncio.fixture
async def client(setup_public_tables):
    """Async HTTP client for the public-only FastAPI app."""
    app = _create_public_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# 1. Crisis endpoint returns 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crisis_endpoint_returns_200(client: AsyncClient):
    resp = await client.get("/api/v1/public/crisis")
    assert resp.status_code == 200
    data = resp.json()
    # Response should contain a helplines list
    assert "helplines" in data
    assert isinstance(data["helplines"], list)
    assert len(data["helplines"]) > 0


# ---------------------------------------------------------------------------
# 2. Articles endpoint returns 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_articles_endpoint_returns_200(client: AsyncClient):
    resp = await client.get("/api/v1/public/articles")
    assert resp.status_code == 200
    data = resp.json()
    # Paginated response must have an "items" key with a list
    assert "items" in data
    assert isinstance(data["items"], list)


# ---------------------------------------------------------------------------
# 3. Resources endpoint returns 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resources_endpoint_returns_200(client: AsyncClient):
    resp = await client.get("/api/v1/public/resources")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# 4. Workshops endpoint returns 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workshops_endpoint_returns_200(client: AsyncClient):
    resp = await client.get("/api/v1/public/workshops")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# 5. Counselors endpoint returns 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_counselors_endpoint_returns_200(client: AsyncClient):
    resp = await client.get("/api/v1/public/counselors")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# 6. Public endpoints do NOT leak PII
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_public_endpoints_no_pii(client: AsyncClient):
    """
    Verify that the articles response does not contain PII patterns:
    email addresses, phone numbers from clinical records, or ARK-XXXX case numbers.
    """
    resp = await client.get("/api/v1/public/articles")
    assert resp.status_code == 200
    body = resp.text

    # No email addresses
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}", body), (
        "Public articles response should not contain email addresses"
    )

    # No ARK-XXXX case numbers (clinical identifiers)
    assert not re.search(r"ARK-\d{4}", body), (
        "Public articles response should not contain ARK case numbers"
    )

    # No 10-digit Indian mobile numbers in a suspicious context
    # (We check for standalone 10-digit numbers that look like phone numbers)
    assert not re.search(r"\b[6-9]\d{9}\b", body), (
        "Public articles response should not contain Indian mobile phone numbers"
    )


# ---------------------------------------------------------------------------
# 7. Crisis response contains known helpline numbers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crisis_response_contains_helpline_numbers(client: AsyncClient):
    """
    The crisis endpoint must include the iCall number (9152987821)
    and the emergency number (112).
    """
    resp = await client.get("/api/v1/public/crisis")
    assert resp.status_code == 200
    body = resp.text

    assert "9152987821" in body, "Crisis response must include iCall helpline number"
    assert "112" in body, "Crisis response must include India emergency number"
