"""Pytest configuration and fixtures for auth/RBAC tests."""

import asyncio
import json
import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import JSON, String, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.auth.models import Role, User, RefreshToken, UserAttribute
from app.auth.password import hash_password
from app.cases.models import ChildCase, CaseAssignment, CaseNote, AuditLog

# Import all models so SQLAlchemy metadata is complete for table creation
import app.assessments.models  # noqa: F401
import app.messaging.models  # noqa: F401
import app.scheduling.models  # noqa: F401
import app.payments.models  # noqa: F401
import app.admin.models  # noqa: F401
import app.public.models  # noqa: F401


# ---------------------------------------------------------------------------
# SQLite type compatibility — map PostgreSQL-only types to SQLite equivalents
# ---------------------------------------------------------------------------

from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID


@event.listens_for(Base.metadata, "column_reflect")
def _column_reflect(inspector, table, column_info):
    pass


# Monkey-patch JSONB and UUID compilation for SQLite
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(type_, compiler, **kw):
    return "VARCHAR(36)"


# ---------------------------------------------------------------------------
# In-memory SQLite engine for tests
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


def _uuid_aware_serializer(obj):
    """JSON serializer that handles UUID, datetime, date objects for SQLite."""
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
    json_serializer=lambda obj: json.dumps(obj, default=_uuid_aware_serializer),
)

TestSessionLocal = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest.fixture(scope="session")
def event_loop():
    """Override default event loop for session-scoped async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def setup_database():
    """Create all tables once per test session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db(setup_database):
    """Provide a transactional database session for each test."""
    async with TestSessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def roles(db: AsyncSession):
    """Create all 8 roles (or return existing ones)."""
    from sqlalchemy import select

    role_names = [
        "super_admin", "admin", "chief_therapist", "supervisor",
        "therapist", "nurturer", "staff", "parent",
    ]
    roles = {}
    for name in role_names:
        result = await db.execute(select(Role).where(Role.name == name))
        existing = result.scalar_one_or_none()
        if existing:
            roles[name] = existing
        else:
            role = Role(name=name, description=f"{name} role")
            db.add(role)
            roles[name] = role
    await db.commit()
    for r in roles.values():
        await db.refresh(r)
    return roles


@pytest_asyncio.fixture
async def users(db: AsyncSession, roles: dict):
    """Create one user per role (or return existing ones)."""
    from sqlalchemy import select

    created = {}
    for role_name, role in roles.items():
        email = f"{role_name}@thearktrust.org"
        result = await db.execute(select(User).where(User.email == email))
        existing = result.scalar_one_or_none()
        if existing:
            created[role_name] = existing
        else:
            user = User(
                email=email,
                hashed_password=hash_password("TestPass123!"),
                full_name=f"Test {role_name.replace('_', ' ').title()}",
                role_id=role.id,
                is_active=True,
                is_verified=True,
            )
            db.add(user)
            created[role_name] = user
    await db.commit()
    for u in created.values():
        await db.refresh(u)
    return created


@pytest_asyncio.fixture
async def child_case(db: AsyncSession, users: dict):
    """Create a test child case (or return existing one)."""
    from sqlalchemy import select

    result = await db.execute(select(ChildCase).where(ChildCase.case_number == "ARK-TEST-001"))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    case = ChildCase(
        case_number="ARK-TEST-001",
        first_name="Test",
        last_name="Child",
        date_of_birth="2015-06-15",
        status="active",
        created_by=users["admin"].id,
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return case


@pytest_asyncio.fixture
async def case_assignment(db: AsyncSession, users: dict, child_case: ChildCase):
    """Assign the therapist user to the test case (or return existing)."""
    from sqlalchemy import select

    result = await db.execute(
        select(CaseAssignment).where(
            CaseAssignment.case_id == child_case.id,
            CaseAssignment.user_id == users["therapist"].id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    assignment = CaseAssignment(
        case_id=child_case.id,
        user_id=users["therapist"].id,
        assignment_type="primary_therapist",
        assigned_by=users["admin"].id,
        is_active=True,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


@pytest_asyncio.fixture
async def supervisor_assignment(db: AsyncSession, users: dict, child_case: ChildCase):
    """Assign the supervisor user to the test case (or return existing)."""
    from sqlalchemy import select

    result = await db.execute(
        select(CaseAssignment).where(
            CaseAssignment.case_id == child_case.id,
            CaseAssignment.user_id == users["supervisor"].id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    assignment = CaseAssignment(
        case_id=child_case.id,
        user_id=users["supervisor"].id,
        assignment_type="supervisor",
        assigned_by=users["admin"].id,
        is_active=True,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


@pytest_asyncio.fixture
async def parent_with_case(db: AsyncSession, users: dict, child_case: ChildCase):
    """Link the parent user to the test child case (or return existing)."""
    from sqlalchemy import select

    result = await db.execute(
        select(UserAttribute).where(
            UserAttribute.user_id == users["parent"].id,
            UserAttribute.attribute_key == "child_case_id",
            UserAttribute.attribute_value == str(child_case.id),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return users["parent"]

    attr = UserAttribute(
        user_id=users["parent"].id,
        attribute_key="child_case_id",
        attribute_value=str(child_case.id),
    )
    db.add(attr)
    await db.commit()
    return users["parent"]
