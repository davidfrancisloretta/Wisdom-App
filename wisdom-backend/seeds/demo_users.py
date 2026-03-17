"""Seed demo users for all 8 roles.

Usage:
    cd wisdom-backend
    python -m seeds.demo_users

All users use password: DemoPass123!
"""

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from app.database import AsyncSessionLocal, engine, Base
from app.auth.models import Role, User
from app.auth.password import hash_password

# Import all models so metadata is complete
import app.auth.models  # noqa: F401
import app.cases.models  # noqa: F401
import app.assessments.models  # noqa: F401
import app.messaging.models  # noqa: F401
import app.scheduling.models  # noqa: F401
import app.payments.models  # noqa: F401
import app.admin.models  # noqa: F401

DEMO_PASSWORD = "DemoPass123!"

ROLES = [
    ("super_admin", "Super Administrator — full system access"),
    ("admin", "Administrator — case & user management"),
    ("chief_therapist", "Chief Therapist — oversees all therapists and cases"),
    ("supervisor", "Supervisor — supervises assigned therapists"),
    ("therapist", "Therapist — direct client care"),
    ("nurturer", "Nurturer — support role with limited PII access"),
    ("staff", "Staff — scheduling and payments"),
    ("parent", "Parent / Caregiver — assessment completion"),
]

DEMO_USERS = [
    {
        "email": "super_admin@thearktrust.org",
        "full_name": "Sarah Admin (Super)",
        "phone": "+91-9000000001",
        "role": "super_admin",
    },
    {
        "email": "admin@thearktrust.org",
        "full_name": "Anita Sharma",
        "phone": "+91-9000000002",
        "role": "admin",
    },
    {
        "email": "chief_therapist@thearktrust.org",
        "full_name": "Dr. Priya Nair",
        "phone": "+91-9000000003",
        "role": "chief_therapist",
    },
    {
        "email": "supervisor@thearktrust.org",
        "full_name": "Ramesh Iyer",
        "phone": "+91-9000000004",
        "role": "supervisor",
    },
    {
        "email": "therapist@thearktrust.org",
        "full_name": "Kavitha Menon",
        "phone": "+91-9000000005",
        "role": "therapist",
    },
    {
        "email": "nurturer@thearktrust.org",
        "full_name": "Meera Pillai",
        "phone": "+91-9000000006",
        "role": "nurturer",
    },
    {
        "email": "staff@thearktrust.org",
        "full_name": "Joseph Thomas",
        "phone": "+91-9000000007",
        "role": "staff",
    },
    {
        "email": "parent@thearktrust.org",
        "full_name": "Lakshmi Devi",
        "phone": "+91-9000000008",
        "role": "parent",
    },
]


async def seed():
    hashed = hash_password(DEMO_PASSWORD)

    async with AsyncSessionLocal() as db:
        # --- Ensure roles exist ---
        for role_name, description in ROLES:
            existing = await db.execute(select(Role).where(Role.name == role_name))
            if existing.scalar_one_or_none() is None:
                db.add(Role(id=uuid.uuid4(), name=role_name, description=description, is_system_role=True))
                print(f"  + Role: {role_name}")
            else:
                print(f"  = Role: {role_name} (exists)")
        await db.commit()

        # --- Create demo users ---
        for user_data in DEMO_USERS:
            existing = await db.execute(select(User).where(User.email == user_data["email"]))
            if existing.scalar_one_or_none() is not None:
                print(f"  = User: {user_data['email']} (exists)")
                continue

            # Look up role
            role_result = await db.execute(select(Role).where(Role.name == user_data["role"]))
            role = role_result.scalar_one()

            db.add(
                User(
                    id=uuid.uuid4(),
                    email=user_data["email"],
                    hashed_password=hashed,
                    full_name=user_data["full_name"],
                    phone=user_data["phone"],
                    role_id=role.id,
                    is_active=True,
                    is_verified=True,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            print(f"  + User: {user_data['email']} ({user_data['role']})")

        await db.commit()

    print("\nDone! All demo users seeded.")
    print(f"Password for all accounts: {DEMO_PASSWORD}")


if __name__ == "__main__":
    print("Seeding demo users...\n")
    asyncio.run(seed())
