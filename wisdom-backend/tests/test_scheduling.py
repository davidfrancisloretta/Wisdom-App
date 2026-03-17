"""Tests for scheduling module -- room bookings, conflicts, maintenance."""
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TestSessionLocal, test_engine
from app.database import Base, get_db
from app.auth.jwt import create_access_token
from app.scheduling.models import Room, RoomBooking, MaintenanceWindow


# ---------------------------------------------------------------------------
# Test-scoped app with scheduling + auth routers
# ---------------------------------------------------------------------------

def _create_scheduling_app():
    """Create a FastAPI app with scheduling and auth routers for tests."""
    from fastapi import FastAPI
    from app.auth.router import router as auth_router
    from app.scheduling.router import router as scheduling_router

    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1/auth")
    app.include_router(scheduling_router, prefix="/api/v1/scheduling")

    async def _override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    return app


_test_app = _create_scheduling_app()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_cookies(client, users):
    """Login as admin and return cookies for authenticated requests."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@thearktrust.org", "password": "TestPass123!"},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.cookies


@pytest_asyncio.fixture
async def test_room(db: AsyncSession):
    """Create a test room with 15 minutes prep time."""
    from sqlalchemy import select

    # Return existing if found
    result = await db.execute(select(Room).where(Room.name == "Therapy Room A"))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    room = Room(
        name="Therapy Room A",
        room_type="therapy",
        capacity=4,
        floor="1",
        is_active=True,
        prep_time_minutes=15,
    )
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return room


# ===========================================================================
# SCHEDULING TESTS
# ===========================================================================


class TestBookingConflicts:
    """Tests for overlapping booking conflict detection."""

    @pytest.mark.asyncio
    async def test_book_room_overlapping_time(
        self, client, auth_cookies, test_room, users
    ):
        """Create a booking, then try to create an overlapping booking in the
        same room. Expect 409 conflict."""
        base_time = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=10)

        # First booking: 10:00 - 11:00
        booking1 = {
            "room_id": str(test_room.id),
            "booking_type": "therapy",
            "title": "Session Alpha",
            "start_datetime": (base_time + timedelta(hours=10)).isoformat(),
            "end_datetime": (base_time + timedelta(hours=11)).isoformat(),
        }
        resp1 = await client.post(
            "/api/v1/scheduling/bookings",
            json=booking1,
            cookies=auth_cookies,
        )
        assert resp1.status_code == 201, f"First booking failed: {resp1.text}"

        # Second booking overlaps: 10:30 - 11:30
        booking2 = {
            "room_id": str(test_room.id),
            "booking_type": "therapy",
            "title": "Session Beta",
            "start_datetime": (base_time + timedelta(hours=10, minutes=30)).isoformat(),
            "end_datetime": (base_time + timedelta(hours=11, minutes=30)).isoformat(),
        }
        resp2 = await client.post(
            "/api/v1/scheduling/bookings",
            json=booking2,
            cookies=auth_cookies,
        )
        assert resp2.status_code == 409, (
            f"Expected 409 conflict, got {resp2.status_code}: {resp2.text}"
        )

    @pytest.mark.asyncio
    async def test_book_room_prep_time_overlap(
        self, client, auth_cookies, test_room, users
    ):
        """Create a booking, then try to book during the prep time window.
        The room has 15 min prep time, so a booking ending exactly when the
        next starts should conflict because of the prep buffer."""
        base_time = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=11)

        # First booking: 14:00 - 15:00
        booking1 = {
            "room_id": str(test_room.id),
            "booking_type": "therapy",
            "title": "Session with Prep",
            "start_datetime": (base_time + timedelta(hours=14)).isoformat(),
            "end_datetime": (base_time + timedelta(hours=15)).isoformat(),
        }
        resp1 = await client.post(
            "/api/v1/scheduling/bookings",
            json=booking1,
            cookies=auth_cookies,
        )
        assert resp1.status_code == 201, f"First booking failed: {resp1.text}"

        # Second booking starts within 15-min prep window of first: 14:50 - 15:50
        # The conflict check adjusts the query window backward by prep_time_minutes,
        # so booking starting at 14:50 overlaps with 14:00-15:00 adjusted to
        # 13:45-15:00 query window. This actually directly overlaps, but the
        # important test is that a booking at 15:05 (within prep window) conflicts.
        booking2 = {
            "room_id": str(test_room.id),
            "booking_type": "therapy",
            "title": "Session During Prep",
            "start_datetime": (base_time + timedelta(hours=15, minutes=5)).isoformat(),
            "end_datetime": (base_time + timedelta(hours=16)).isoformat(),
        }
        resp2 = await client.post(
            "/api/v1/scheduling/bookings",
            json=booking2,
            cookies=auth_cookies,
        )
        # The conflict detection expands the query window backwards by prep_time_minutes.
        # Booking2 start=15:05, adjusted_start=15:05-15min=14:50. The existing
        # booking 14:00-15:00 ends at 15:00 which is > adjusted_start 14:50,
        # so it overlaps => conflict detected.
        assert resp2.status_code == 409, (
            f"Expected 409 conflict due to prep time overlap, got {resp2.status_code}: {resp2.text}"
        )

    @pytest.mark.asyncio
    async def test_book_room_adjacent_no_conflict(
        self, client, auth_cookies, db, users
    ):
        """Create a booking, then create another that starts exactly when the
        first ends. Expect success (no conflict) when prep_time is 0."""
        from sqlalchemy import select

        # Create a room with 0 prep time for this test
        result = await db.execute(select(Room).where(Room.name == "No-Prep Room"))
        room = result.scalar_one_or_none()
        if not room:
            room = Room(
                name="No-Prep Room",
                room_type="training",
                capacity=10,
                is_active=True,
                prep_time_minutes=0,
            )
            db.add(room)
            await db.commit()
            await db.refresh(room)

        base_time = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=12)

        # First booking: 09:00 - 10:00
        booking1 = {
            "room_id": str(room.id),
            "booking_type": "training",
            "title": "Morning Training",
            "start_datetime": (base_time + timedelta(hours=9)).isoformat(),
            "end_datetime": (base_time + timedelta(hours=10)).isoformat(),
        }
        resp1 = await client.post(
            "/api/v1/scheduling/bookings",
            json=booking1,
            cookies=auth_cookies,
        )
        assert resp1.status_code == 201, f"First booking failed: {resp1.text}"

        # Second booking starts exactly when first ends: 10:00 - 11:00
        booking2 = {
            "room_id": str(room.id),
            "booking_type": "training",
            "title": "Late Morning Training",
            "start_datetime": (base_time + timedelta(hours=10)).isoformat(),
            "end_datetime": (base_time + timedelta(hours=11)).isoformat(),
        }
        resp2 = await client.post(
            "/api/v1/scheduling/bookings",
            json=booking2,
            cookies=auth_cookies,
        )
        assert resp2.status_code == 201, (
            f"Adjacent booking should succeed, got {resp2.status_code}: {resp2.text}"
        )


class TestRecurringBookings:
    """Tests for recurring booking series."""

    @pytest.mark.asyncio
    async def test_recurring_series_with_conflict(
        self, client, auth_cookies, db, users
    ):
        """Create a booking, then create a weekly recurring series where one
        instance conflicts. Expect entire series rejected with conflict dates."""
        from sqlalchemy import select

        # Create a dedicated room for this test
        result = await db.execute(select(Room).where(Room.name == "Recurring Test Room"))
        room = result.scalar_one_or_none()
        if not room:
            room = Room(
                name="Recurring Test Room",
                room_type="therapy",
                capacity=6,
                is_active=True,
                prep_time_minutes=0,
            )
            db.add(room)
            await db.commit()
            await db.refresh(room)

        # Base time: next Monday at 10:00 UTC
        now = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # Go forward to find a Monday (weekday 0)
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        next_monday = now + timedelta(days=days_until_monday + 14)  # +14 for buffer

        # Create a single booking on week 2's slot: blocks that time
        blocker_start = next_monday + timedelta(weeks=1, hours=10)
        blocker_end = next_monday + timedelta(weeks=1, hours=11)
        blocker = {
            "room_id": str(room.id),
            "booking_type": "therapy",
            "title": "Blocker Session",
            "start_datetime": blocker_start.isoformat(),
            "end_datetime": blocker_end.isoformat(),
        }
        resp_blocker = await client.post(
            "/api/v1/scheduling/bookings",
            json=blocker,
            cookies=auth_cookies,
        )
        assert resp_blocker.status_code == 201, f"Blocker booking failed: {resp_blocker.text}"

        # Now try to create a weekly recurring series starting week 1
        # RRULE: weekly for 4 weeks
        series_start = next_monday + timedelta(hours=10)
        series_end = next_monday + timedelta(hours=11)
        recurring = {
            "room_id": str(room.id),
            "booking_type": "therapy",
            "title": "Weekly Therapy",
            "start_datetime": series_start.isoformat(),
            "end_datetime": series_end.isoformat(),
            "recurrence_rule": f"RRULE:FREQ=WEEKLY;COUNT=4",
        }
        resp_series = await client.post(
            "/api/v1/scheduling/bookings",
            json=recurring,
            cookies=auth_cookies,
        )
        # The second instance conflicts with the blocker
        assert resp_series.status_code == 409, (
            f"Expected 409 for conflicting recurring series, got {resp_series.status_code}: {resp_series.text}"
        )
        # The error detail should mention the conflicting slot
        assert "Conflict" in resp_series.json().get("detail", ""), (
            "Error detail should mention conflict"
        )

    @pytest.mark.asyncio
    async def test_cancel_single_recurring_instance(
        self, client, auth_cookies, db, users
    ):
        """Create a recurring series, then cancel one instance. Only that
        instance should be cancelled; the rest remain confirmed."""
        from sqlalchemy import select

        # Create a dedicated room
        result = await db.execute(select(Room).where(Room.name == "Cancel Test Room"))
        room = result.scalar_one_or_none()
        if not room:
            room = Room(
                name="Cancel Test Room",
                room_type="therapy",
                capacity=4,
                is_active=True,
                prep_time_minutes=0,
            )
            db.add(room)
            await db.commit()
            await db.refresh(room)

        # Schedule a recurring series: 3 weeks
        now = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        days_until_wed = (2 - now.weekday()) % 7
        if days_until_wed == 0:
            days_until_wed = 7
        next_wed = now + timedelta(days=days_until_wed + 21)  # buffer

        series_start = next_wed + timedelta(hours=14)
        series_end = next_wed + timedelta(hours=15)
        recurring = {
            "room_id": str(room.id),
            "booking_type": "therapy",
            "title": "Recurring for Cancel Test",
            "start_datetime": series_start.isoformat(),
            "end_datetime": series_end.isoformat(),
            "recurrence_rule": "RRULE:FREQ=WEEKLY;COUNT=3",
        }
        resp = await client.post(
            "/api/v1/scheduling/bookings",
            json=recurring,
            cookies=auth_cookies,
        )
        assert resp.status_code == 201, f"Recurring booking failed: {resp.text}"
        bookings = resp.json()
        assert len(bookings) == 3, f"Expected 3 instances, got {len(bookings)}"

        # Cancel the second instance only (cancel_series=false)
        second_id = bookings[1]["id"]
        cancel_resp = await client.delete(
            f"/api/v1/scheduling/bookings/{second_id}?cancel_series=false",
            cookies=auth_cookies,
        )
        assert cancel_resp.status_code == 200, f"Cancel failed: {cancel_resp.text}"
        assert cancel_resp.json()["status"] == "cancelled"

        # Verify the first and third are still confirmed
        for idx in [0, 2]:
            get_resp = await client.get(
                f"/api/v1/scheduling/bookings/{bookings[idx]['id']}",
                cookies=auth_cookies,
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["status"] == "confirmed", (
                f"Booking {idx} should still be confirmed"
            )

        # Verify the second is cancelled
        get_cancelled = await client.get(
            f"/api/v1/scheduling/bookings/{second_id}",
            cookies=auth_cookies,
        )
        assert get_cancelled.status_code == 200
        assert get_cancelled.json()["status"] == "cancelled"


class TestMaintenanceWindow:
    """Tests for maintenance window blocking bookings."""

    @pytest.mark.asyncio
    async def test_maintenance_window_blocks_booking(
        self, client, auth_cookies, test_room, users
    ):
        """Create a maintenance window, then try to book the same room during
        that time. Expect conflict with maintenance window."""
        base_time = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=20)

        # Create maintenance window: 08:00 - 12:00
        mw_data = {
            "start_datetime": (base_time + timedelta(hours=8)).isoformat(),
            "end_datetime": (base_time + timedelta(hours=12)).isoformat(),
            "reason": "Deep cleaning and sanitisation",
        }
        mw_resp = await client.post(
            f"/api/v1/scheduling/rooms/{test_room.id}/maintenance",
            json=mw_data,
            cookies=auth_cookies,
        )
        assert mw_resp.status_code == 201, f"Maintenance creation failed: {mw_resp.text}"

        # Try to book during maintenance: 09:00 - 10:00
        booking = {
            "room_id": str(test_room.id),
            "booking_type": "therapy",
            "title": "Blocked by Maintenance",
            "start_datetime": (base_time + timedelta(hours=9)).isoformat(),
            "end_datetime": (base_time + timedelta(hours=10)).isoformat(),
        }
        book_resp = await client.post(
            "/api/v1/scheduling/bookings",
            json=booking,
            cookies=auth_cookies,
        )
        assert book_resp.status_code == 409, (
            f"Expected 409 conflict with maintenance window, "
            f"got {book_resp.status_code}: {book_resp.text}"
        )
        detail = book_resp.json().get("detail", "")
        assert "Conflict" in detail or "conflict" in detail.lower(), (
            "Error should mention conflict"
        )
