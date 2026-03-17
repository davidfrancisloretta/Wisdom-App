"""WhatsApp marketing campaign broadcasts."""
import asyncio
import logging
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.whatsapp import send_whatsapp_template

logger = logging.getLogger(__name__)


async def send_campaign_broadcast(
    template_name: str,
    recipient_list: list[str],
    template_params: list[str],
    campaign_name: str,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Send a WhatsApp template message to a list of recipients.
    Rate limit: batch into groups of 50 with 1-second delay between batches.
    Track delivery via WhatsAppMessage records.
    """
    total = len(recipient_list)
    queued = 0
    failed = 0
    batch_size = 50

    for i in range(0, total, batch_size):
        batch = recipient_list[i:i + batch_size]
        for phone in batch:
            try:
                await send_whatsapp_template(
                    recipient_phone=phone,
                    template_name=template_name,
                    template_params=template_params,
                    case_id=None,
                    risk_alert_id=None,
                    db=db,
                )
                queued += 1
            except Exception as e:
                logger.error(f"Campaign '{campaign_name}' failed for {phone}: {e}")
                failed += 1

        # Rate limit: 1-second delay between batches
        if i + batch_size < total:
            await asyncio.sleep(1)

    logger.info(f"Campaign '{campaign_name}': total={total}, queued={queued}, failed={failed}")
    return {"total": total, "queued": queued, "failed": failed, "campaign_name": campaign_name}


async def get_active_parent_phones(db: AsyncSession) -> list[str]:
    """Get phone numbers of guardians for all active cases."""
    from app.cases.models import ChildCase
    from app.security.encryption import decrypt_value

    result = await db.execute(
        select(ChildCase).where(ChildCase.status == "active")
    )
    cases = result.scalars().all()

    phones = []
    for case in cases:
        if case.guardian_phone:
            try:
                phone = decrypt_value(case.guardian_phone)
                if phone:
                    phones.append(phone)
            except Exception:
                pass

    return list(set(phones))  # deduplicate


async def get_opted_in_donor_phones(db: AsyncSession) -> list[str]:
    """Get phone numbers of donors who have donated (opted in by donating)."""
    from app.payments.models import Donation

    result = await db.execute(
        select(Donation.donor_phone).where(
            Donation.donor_phone.isnot(None),
            Donation.status == "captured",
        ).distinct()
    )
    phones = [row[0] for row in result.all() if row[0]]
    return phones
