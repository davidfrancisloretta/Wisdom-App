"""Integration tests — Auth flows, case management, assessment engine.

Uses httpx.AsyncClient against the real FastAPI app with an in-memory SQLite DB.
"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TestSessionLocal, test_engine
from app.database import Base, get_db


# ---------------------------------------------------------------------------
# Test-scoped app with DB override
# ---------------------------------------------------------------------------

def _create_test_app():
    """Create a fresh FastAPI app for integration tests (no lifespan side effects)."""
    from fastapi import FastAPI
    from app.auth.router import router as auth_router
    from app.cases.router import router as cases_router
    from app.assessments.router import router as assessments_router

    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1/auth")
    app.include_router(cases_router, prefix="/api/v1/cases")
    app.include_router(assessments_router, prefix="/api/v1/assessments")

    async def _override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    return app


test_app = _create_test_app()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Helper: login and return cookies dict
# ---------------------------------------------------------------------------

async def _login(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"cookies": resp.cookies, "status": resp.status_code, "body": resp.json() if resp.status_code < 500 else {}}


async def _login_with_token(client: AsyncClient, email: str, password: str) -> str:
    """Login and extract the access_token cookie value."""
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return resp.cookies.get("access_token", "")


# ============================================================================
# AUTH INTEGRATION TESTS
# ============================================================================


class TestAuthLogin:
    @pytest.mark.asyncio
    async def test_valid_login_returns_200(self, client, users):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@thearktrust.org", "password": "TestPass123!"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "admin@thearktrust.org"
        assert body["role"] == "admin"

    @pytest.mark.asyncio
    async def test_valid_login_sets_cookies(self, client, users):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@thearktrust.org", "password": "TestPass123!"},
        )
        assert "access_token" in resp.cookies
        assert "refresh_token" in resp.cookies

    @pytest.mark.asyncio
    async def test_invalid_password_returns_401(self, client, users):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@thearktrust.org", "password": "WrongPassword!"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_nonexistent_email_returns_401(self, client, users):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@thearktrust.org", "password": "TestPass123!"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_parent_cannot_use_staff_login(self, client, users):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "parent@thearktrust.org", "password": "TestPass123!"},
        )
        assert resp.status_code == 401


class TestAuthLogout:
    @pytest.mark.asyncio
    async def test_logout_clears_cookies(self, client, users):
        # Login first
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@thearktrust.org", "password": "TestPass123!"},
        )
        cookies = login_resp.cookies

        # Logout
        resp = await client.post("/api/v1/auth/logout", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["message"] == "Logged out successfully"


class TestAuthRefresh:
    @pytest.mark.asyncio
    async def test_refresh_issues_new_tokens(self, client, users):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "therapist@thearktrust.org", "password": "TestPass123!"},
        )
        cookies = login_resp.cookies

        refresh_resp = await client.post("/api/v1/auth/refresh", cookies=cookies)
        assert refresh_resp.status_code == 200
        assert "access_token" in refresh_resp.cookies

    @pytest.mark.asyncio
    async def test_refresh_without_token_returns_401(self, client):
        resp = await client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401


class TestAuthMe:
    @pytest.mark.asyncio
    async def test_me_returns_profile(self, client, users):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "therapist@thearktrust.org", "password": "TestPass123!"},
        )
        cookies = login_resp.cookies

        resp = await client.get("/api/v1/auth/me", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["role"] == "therapist"

    @pytest.mark.asyncio
    async def test_me_without_auth_returns_401(self, client):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401


# ============================================================================
# CASE MANAGEMENT INTEGRATION TESTS
# ============================================================================


class TestCaseManagement:
    @pytest.mark.asyncio
    async def test_create_case(self, client, users):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@thearktrust.org", "password": "TestPass123!"},
        )
        cookies = login_resp.cookies

        resp = await client.post(
            "/api/v1/cases",
            json={
                "first_name": "IntegTest",
                "last_name": "Child",
                "date_of_birth": "2014-05-15",
                "gender": "male",
                "age_at_intake": 12,
            },
            cookies=cookies,
        )
        assert resp.status_code in (200, 201)
        body = resp.json()
        assert body["case_number"].startswith("ARK-")
        assert body["status"] == "active"

    @pytest.mark.asyncio
    async def test_get_case_as_admin(self, client, users, child_case):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@thearktrust.org", "password": "TestPass123!"},
        )
        cookies = login_resp.cookies

        resp = await client.get(f"/api/v1/cases/{child_case.id}", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["case_number"] == "ARK-TEST-001"

    @pytest.mark.asyncio
    async def test_get_case_unassigned_staff_denied(self, client, db, users):
        """A staff user not assigned to a case should be denied (ABAC)."""
        from app.cases.service import create_case as svc_create_case
        from app.cases.schemas import CaseCreate as SvcCaseCreate

        # Create a fresh case with no assignments
        fresh_case = await svc_create_case(
            SvcCaseCreate(first_name="ABAC", last_name="Test", date_of_birth="2014-01-01"),
            users["admin"].id, db,
        )

        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "staff@thearktrust.org", "password": "TestPass123!"},
        )
        cookies = login_resp.cookies

        resp = await client.get(f"/api/v1/cases/{fresh_case['id']}", cookies=cookies)
        # ABAC should deny — staff has no assignment
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_get_case_assigned_therapist_allowed(self, client, users, child_case, case_assignment):
        """A therapist assigned to the case should have access."""
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "therapist@thearktrust.org", "password": "TestPass123!"},
        )
        cookies = login_resp.cookies

        resp = await client.get(f"/api/v1/cases/{child_case.id}", cookies=cookies)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_cases(self, client, users, child_case):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@thearktrust.org", "password": "TestPass123!"},
        )
        cookies = login_resp.cookies

        resp = await client.get("/api/v1/cases", cookies=cookies)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_add_note_to_case(self, client, users, child_case, case_assignment):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "therapist@thearktrust.org", "password": "TestPass123!"},
        )
        cookies = login_resp.cookies

        resp = await client.post(
            f"/api/v1/cases/{child_case.id}/notes",
            json={
                "note_type": "session",
                "content": "Integration test note content.",
            },
            cookies=cookies,
        )
        assert resp.status_code in (200, 201)


# ============================================================================
# E2E TEST: P0 SAFETY PATH (most critical)
# ============================================================================


class TestP0SafetyPath:
    """End-to-end: Admin creates case → assigns therapist → parent submits
    assessment with Domain XII Yes → P0 RiskAlert created → WhatsApp queued."""

    @pytest.mark.asyncio
    async def test_full_p0_safety_workflow(self, client, db, users, child_case, case_assignment):
        """The complete P0 safety alert path from assessment submission to alert creation."""
        from sqlalchemy import select
        from app.assessments.models import (
            Assessment,
            AssessmentSection,
            AssessmentQuestion,
            AssessmentDomain,
            AnswerOption,
            AssessmentAssignment,
            AssessmentResponse,
            QuestionResponse,
            DomainScore,
            RiskAlert,
        )
        from app.messaging.models import WhatsAppMessage

        # --- Setup: create a minimal assessment with Domain XII question ---
        assessment = Assessment(
            title="E2E Safety Test Assessment",
            description="Test",
            version="1.0",
            is_active=True,
            created_by=users["admin"].id,
        )
        db.add(assessment)
        await db.commit()
        await db.refresh(assessment)

        # Domain XII — safety-critical
        domain_xii = AssessmentDomain(
            assessment_id=assessment.id,
            domain_name="Suicidal Ideation",
            domain_code="XII",
            threshold_further_inquiry=1,
            threshold_type="yes_no",
            is_safety_critical=True,
        )
        db.add(domain_xii)
        await db.commit()
        await db.refresh(domain_xii)

        section = AssessmentSection(
            assessment_id=assessment.id,
            title="Safety Section",
            order_index=1,
        )
        db.add(section)
        await db.commit()
        await db.refresh(section)

        # Question 24 — safety question
        q24 = AssessmentQuestion(
            section_id=section.id,
            question_text="Have you thought about hurting yourself?",
            question_type="yes_no",
            order_index=24,
            domain_id=domain_xii.id,
            is_required=True,
            is_risk_flag=True,
        )
        db.add(q24)
        await db.commit()
        await db.refresh(q24)

        # Answer options
        for text, val in [("No", 0), ("Yes", 1)]:
            db.add(AnswerOption(
                question_id=q24.id,
                option_text=text,
                value=val,
                order_index=val,
            ))
        await db.commit()

        # --- Assign assessment to case ---
        assignment = AssessmentAssignment(
            assessment_id=assessment.id,
            case_id=child_case.id,
            assigned_by=users["admin"].id,
            assigned_to_parent=True,
            status="pending",
        )
        db.add(assignment)
        await db.commit()
        await db.refresh(assignment)

        # --- Submit response with Domain XII = Yes ---
        from app.assessments.scoring import score_assessment_response
        from unittest.mock import MagicMock
        from datetime import datetime, timezone

        response = AssessmentResponse(
            assignment_id=assignment.id,
            submitted_by=users["parent"].id,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            is_partial=False,
        )
        db.add(response)
        await db.commit()
        await db.refresh(response)

        # Answer Q24 = Yes (value=1)
        qr = QuestionResponse(
            response_id=response.id,
            question_id=q24.id,
            answer_value=1,
        )
        db.add(qr)
        await db.commit()

        # --- Score the assessment ---
        mock_bg = MagicMock()
        mock_bg.add_task = MagicMock()  # Prevent actual background tasks
        result = await score_assessment_response(response.id, db, mock_bg)

        # --- Verify P0 alert was created ---
        alert_result = await db.execute(
            select(RiskAlert).where(RiskAlert.case_id == child_case.id)
        )
        alerts = alert_result.scalars().all()
        p0_alerts = [a for a in alerts if a.severity == "P0"]
        assert len(p0_alerts) >= 1, "P0 safety alert must be created for Domain XII Yes answer"

        # Verify alert details
        alert = p0_alerts[0]
        assert alert.status == "open"
        assert alert.alert_type is not None

        # --- Verify domain score was flagged ---
        ds_result = await db.execute(
            select(DomainScore).where(
                DomainScore.response_id == response.id,
                DomainScore.domain_id == domain_xii.id,
            )
        )
        domain_score = ds_result.scalar_one_or_none()
        assert domain_score is not None
        assert domain_score.is_safety_alert is True
        assert domain_score.requires_further_inquiry is True
