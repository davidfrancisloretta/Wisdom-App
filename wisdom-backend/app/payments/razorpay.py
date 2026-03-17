"""Razorpay payment gateway integration."""
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

import razorpay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.payments.models import Invoice, Payment

logger = logging.getLogger(__name__)


def _get_client():
    settings = get_settings()
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


async def create_razorpay_order(amount_paise: int, currency: str, receipt: str) -> dict:
    """Create a Razorpay order."""
    client = _get_client()
    order = client.order.create({
        "amount": amount_paise,
        "currency": currency,
        "receipt": receipt,
    })
    return order


async def create_razorpay_payment_link(
    amount_paise: int,
    description: str,
    customer_name: str,
    customer_phone: str,
    customer_email: str,
    reference_id: str,
) -> str:
    """Create a Razorpay Payment Link and return the short URL."""
    settings = get_settings()
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        logger.warning("Razorpay not configured — returning placeholder link")
        return f"https://rzp.io/placeholder/{reference_id}"

    client = _get_client()
    link_data = {
        "amount": amount_paise,
        "currency": "INR",
        "description": description,
        "customer": {
            "name": customer_name,
            "contact": customer_phone,
            "email": customer_email,
        },
        "reference_id": reference_id,
        "expire_by": int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp()),
        "notify": {"sms": True, "email": True},
    }
    result = client.payment_link.create(link_data)
    return result.get("short_url", "")


async def verify_razorpay_signature(payload: dict, signature: str) -> bool:
    """Verify Razorpay webhook signature."""
    settings = get_settings()
    body = str(payload).encode()
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_razorpay_webhook(
    payload: dict, signature: str, db: AsyncSession, background_tasks
):
    """Process Razorpay webhook events."""
    settings = get_settings()

    # Verify signature
    webhook_body = str(payload).encode()
    expected_sig = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        webhook_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, signature):
        raise ValueError("Invalid Razorpay webhook signature")

    event = payload.get("event", "")
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

    if event == "payment.captured":
        gateway_payment_id = payment_entity.get("id", "")
        amount = payment_entity.get("amount", 0)
        method = payment_entity.get("method", "")

        # Create/update payment record
        result = await db.execute(
            select(Payment).where(Payment.gateway_payment_id == gateway_payment_id)
        )
        payment = result.scalar_one_or_none()
        if not payment:
            payment = Payment(
                gateway="razorpay",
                gateway_payment_id=gateway_payment_id,
                gateway_order_id=payment_entity.get("order_id"),
                amount=amount / 100,
                currency=payment_entity.get("currency", "INR").upper(),
                status="captured",
                method=method,
                captured_at=datetime.now(timezone.utc),
            )
            # Link to invoice if we can find one
            notes = payment_entity.get("notes", {})
            invoice_number = notes.get("invoice_number") or payment_entity.get("description", "")
            if invoice_number:
                inv_result = await db.execute(
                    select(Invoice).where(Invoice.invoice_number == invoice_number)
                )
                invoice = inv_result.scalar_one_or_none()
                if invoice:
                    payment.invoice_id = invoice.id
                    invoice.status = "paid"
                    invoice.paid_at = datetime.now(timezone.utc)
                    invoice.payment_gateway = "razorpay"
                    invoice.gateway_payment_id = gateway_payment_id

            db.add(payment)
        else:
            payment.status = "captured"
            payment.captured_at = datetime.now(timezone.utc)
            payment.method = method
            if payment.invoice_id:
                inv_result = await db.execute(
                    select(Invoice).where(Invoice.id == payment.invoice_id)
                )
                invoice = inv_result.scalar_one_or_none()
                if invoice:
                    invoice.status = "paid"
                    invoice.paid_at = datetime.now(timezone.utc)

        await db.commit()

        # Audit log
        from app.admin.audit_service import log_event
        await log_event(
            user_id=None,
            action="payment_captured",
            resource_type="payment",
            resource_id=gateway_payment_id,
            old_values=None,
            new_values={"amount": amount / 100, "method": method, "gateway": "razorpay"},
            request=None,
            db=db,
        )

    elif event == "payment.failed":
        gateway_payment_id = payment_entity.get("id", "")
        result = await db.execute(
            select(Payment).where(Payment.gateway_payment_id == gateway_payment_id)
        )
        payment = result.scalar_one_or_none()
        if payment:
            payment.status = "failed"
            await db.commit()

    return {"status": "ok"}
