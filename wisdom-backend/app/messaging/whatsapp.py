"""WhatsApp messaging via Meta Cloud API."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx
import sentry_sdk
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.messaging.dead_letter import enqueue_dead_letter
from app.messaging.models import WhatsAppMessage

logger = logging.getLogger(__name__)

WHATSAPP_TEMPLATES = {
    "RISK_ALERT_P0": {"params": ["case_number", "assessment_name", "question_text", "submitted_at"]},
    "APPOINTMENT_REMINDER_24H": {"params": ["child_first_name", "therapist_name", "date", "time", "room_name"]},
    "APPOINTMENT_REMINDER_1H": {"params": ["child_first_name", "date", "time"]},
    "ASSESSMENT_DUE": {"params": ["assessment_title", "due_date", "portal_link"]},
    "INVOICE_SENT": {"params": ["invoice_number", "amount", "due_date", "payment_link"]},
    "DONATION_RECEIPT": {"params": ["donor_name", "amount", "campaign_name", "receipt_number"]},
    "WORKSHOP_INVITATION": {"params": ["workshop_name", "date", "description", "registration_link"]},
}


async def send_whatsapp_template(
    recipient_phone: str,
    template_name: str,
    template_params: list[str],
    case_id: UUID | None,
    risk_alert_id: UUID | None,
    db: AsyncSession,
    max_retries: int = 3,
) -> WhatsAppMessage:
    """
    Send a WhatsApp template message via Meta Cloud API.
    1. Create WhatsAppMessage record with status=queued
    2. Call Meta WhatsApp Cloud API with retries
    3. On success: update status=sent
    4. On failure after retries: DeadLetterQueue + Sentry error
    """
    settings = get_settings()

    # 1. Create message record
    msg = WhatsAppMessage(
        recipient_phone=recipient_phone,
        message_type="template",
        template_name=template_name,
        template_params={"values": template_params},
        status="queued",
        case_id=case_id,
        risk_alert_id=risk_alert_id,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # Skip API call if credentials not configured (dev mode)
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_ID:
        logger.warning("WhatsApp credentials not configured — message queued but not sent")
        return msg

    # 2. Attempt to send with retries
    url = f"https://graph.facebook.com/v21.0/{settings.WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in template_params],
                }
            ],
        },
    }

    last_error = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()

            # Success
            msg.status = "sent"
            msg.whatsapp_message_id = result.get("messages", [{}])[0].get("id")
            msg.sent_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(msg)
            logger.info(f"WhatsApp message sent to {recipient_phone}: {template_name}")
            return msg

        except Exception as e:
            last_error = e
            wait_time = 2 ** attempt  # 1s, 2s, 4s
            logger.warning(f"WhatsApp attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {wait_time}s")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait_time)

    # 5. All retries exhausted
    msg.status = "failed"
    await db.commit()

    await enqueue_dead_letter(
        service="whatsapp",
        payload={
            "recipient_phone": recipient_phone,
            "template_name": template_name,
            "template_params": template_params,
            "message_id": str(msg.id),
        },
        error_message=str(last_error),
        db=db,
    )

    sentry_sdk.capture_exception(last_error)
    logger.error(f"WhatsApp send failed after {max_retries} attempts: {last_error}")

    return msg
