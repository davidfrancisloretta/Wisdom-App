"""Seed all 20 Ark Centre rooms from the Rooms PDF."""

import asyncio
import uuid

from sqlalchemy import select

from app.database import AsyncSessionLocal

# Import all models to ensure SQLAlchemy metadata is fully populated
import app.auth.models  # noqa: F401
import app.cases.models  # noqa: F401
import app.assessments.models  # noqa: F401
import app.payments.models  # noqa: F401
import app.messaging.models  # noqa: F401

from app.scheduling.models import Room

ROOMS = [
    {"name": "Music Therapy Room", "room_type": "therapy", "capacity": 8, "prep_time_minutes": 15},
    {"name": "Art Therapy Room", "room_type": "therapy", "capacity": 10, "prep_time_minutes": 15},
    {"name": "Sensory Room", "room_type": "therapy", "capacity": 6, "prep_time_minutes": 15},
    {"name": "Proprioception Room", "room_type": "therapy", "capacity": 8, "prep_time_minutes": 15},
    {"name": "Counselling Room 1", "room_type": "counselling", "capacity": 3, "prep_time_minutes": 10},
    {"name": "Counselling Room 2", "room_type": "counselling", "capacity": 3, "prep_time_minutes": 10},
    {"name": "Counselling Room 3", "room_type": "counselling", "capacity": 3, "prep_time_minutes": 10},
    {"name": "Counselling Room 4", "room_type": "counselling", "capacity": 3, "prep_time_minutes": 10},
    {"name": "Counselling Room 5", "room_type": "counselling", "capacity": 3, "prep_time_minutes": 10},
    {"name": "Counselling Room 6", "room_type": "counselling", "capacity": 3, "prep_time_minutes": 10},
    {"name": "Training Room 1", "room_type": "training", "capacity": 25, "prep_time_minutes": 20},
    {"name": "Training Room 2", "room_type": "training", "capacity": 25, "prep_time_minutes": 20},
    {"name": "Board Room", "room_type": "meeting", "capacity": 12, "prep_time_minutes": 0},
    {"name": "Auditorium", "room_type": "event", "capacity": 150, "prep_time_minutes": 0},
    {"name": "Hall of Fame", "room_type": "display", "capacity": 30, "prep_time_minutes": 0},
    {"name": "Climbing Wall Area", "room_type": "activity", "capacity": 15, "prep_time_minutes": 0},
    {"name": "Aqua Therapy Pool", "room_type": "therapy", "capacity": 6, "prep_time_minutes": 30},
    {"name": "Office Floor", "room_type": "office", "capacity": 20, "prep_time_minutes": 0},
    {"name": "Washroom Complex Women", "room_type": "facility", "capacity": 0, "prep_time_minutes": 0},
    {"name": "Washroom Complex Men", "room_type": "facility", "capacity": 0, "prep_time_minutes": 0},
]


async def seed_rooms() -> None:
    async with AsyncSessionLocal() as session:
        for room_data in ROOMS:
            existing = await session.execute(
                select(Room).where(Room.name == room_data["name"])
            )
            if existing.scalar_one_or_none() is None:
                room = Room(id=uuid.uuid4(), **room_data)
                session.add(room)
        await session.commit()
        print(f"Seeded {len(ROOMS)} rooms.")


if __name__ == "__main__":
    asyncio.run(seed_rooms())
