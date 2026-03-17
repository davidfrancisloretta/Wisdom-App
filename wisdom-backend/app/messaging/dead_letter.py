"""Dead letter queue for failed async jobs."""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.models import DeadLetterQueue

logger = logging.getLogger(__name__)


async def enqueue_dead_letter(
    service: str,
    payload: dict,
    error_message: str,
    db: AsyncSession,
) -> DeadLetterQueue:
    """Add a failed job to the dead letter queue for later retry."""
    entry = DeadLetterQueue(
        service=service,
        payload=payload,
        error_message=error_message,
        attempt_count=1,
        last_attempted_at=datetime.now(timezone.utc),
        resolved=False,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    logger.warning(f"Dead letter enqueued: service={service}, error={error_message}")
    return entry
