"""Stripe payment gateway integration for international donors."""
import logging
from datetime import datetime, timezone

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.payments.models import Donation, DonationCampaign, Payment

logger = logging.getLogger(__name__)


def _init_stripe():
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY


async def create_stripe_payment_intent(
    amount_cents: int, currency: str, metadata: dict
) -> dict:
    """Create a Stripe PaymentIntent for one-time donations."""
    _init_stripe()
    intent = stripe.PaymentIntent.create(
        amount=amount_cents,
        currency=currency,
        metadata=metadata,
    )
    return {
        "client_secret": intent.client_secret,
        "payment_intent_id": intent.id,
    }


async def create_stripe_subscription(
    customer_email: str, price_id: str, metadata: dict
) -> dict:
    """Create a Stripe subscription for recurring international donations."""
    _init_stripe()
    # Find or create customer
    customers = stripe.Customer.list(email=customer_email, limit=1)
    if customers.data:
        customer = customers.data[0]
    else:
        customer = stripe.Customer.create(email=customer_email, metadata=metadata)

    subscription = stripe.Subscription.create(
        customer=customer.id,
        items=[{"price": price_id}],
        payment_behavior="default_incomplete",
        expand=["latest_invoice.payment_intent"],
        metadata=metadata,
    )
    return {
        "subscription_id": subscription.id,
        "client_secret": subscription.latest_invoice.payment_intent.client_secret,
    }


async def handle_stripe_webhook(
    payload: bytes, signature: str, db: AsyncSession, background_tasks
):
    """Process Stripe webhook events."""
    settings = get_settings()

    try:
        event = stripe.Webhook.construct_event(
            payload, signature, settings.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise ValueError("Invalid Stripe webhook signature")

    if event.type == "payment_intent.succeeded":
        intent = event.data.object
        gateway_payment_id = intent.id
        amount = intent.amount
        currency = intent.currency.upper()
        metadata = intent.metadata or {}

        payment = Payment(
            gateway="stripe",
            gateway_payment_id=gateway_payment_id,
            amount=amount / 100,
            currency=currency,
            status="captured",
            captured_at=datetime.now(timezone.utc),
        )
        db.add(payment)

        # Update donation if linked
        donation_id_str = metadata.get("donation_id")
        if donation_id_str:
            from uuid import UUID as _UUID
            try:
                donation_uuid = _UUID(donation_id_str)
            except (ValueError, AttributeError):
                donation_uuid = None
            if donation_uuid:
                result = await db.execute(
                    select(Donation).where(Donation.id == donation_uuid)
                )
                donation = result.scalar_one_or_none()
                if donation:
                    donation.status = "captured"
                    # Update campaign raised amount
                    if donation.campaign_id:
                        camp_result = await db.execute(
                            select(DonationCampaign).where(DonationCampaign.id == donation.campaign_id)
                        )
                        campaign = camp_result.scalar_one_or_none()
                        if campaign:
                            campaign.raised_amount = float(campaign.raised_amount) + float(donation.amount)

        await db.commit()

        from app.admin.audit_service import log_event
        await log_event(
            user_id=None,
            action="payment_captured",
            resource_type="payment",
            resource_id=gateway_payment_id,
            old_values=None,
            new_values={"amount": amount / 100, "currency": currency, "gateway": "stripe"},
            request=None,
            db=db,
        )

    elif event.type == "invoice.payment_succeeded":
        invoice_obj = event.data.object
        subscription_id = invoice_obj.subscription
        if subscription_id:
            result = await db.execute(
                select(Donation).where(Donation.gateway_subscription_id == subscription_id)
            )
            donation = result.scalar_one_or_none()
            if donation and donation.campaign_id:
                camp_result = await db.execute(
                    select(DonationCampaign).where(DonationCampaign.id == donation.campaign_id)
                )
                campaign = camp_result.scalar_one_or_none()
                if campaign:
                    campaign.raised_amount = float(campaign.raised_amount) + float(donation.amount)
                    await db.commit()

    elif event.type == "customer.subscription.deleted":
        subscription_obj = event.data.object
        result = await db.execute(
            select(Donation).where(Donation.gateway_subscription_id == subscription_obj.id)
        )
        donation = result.scalar_one_or_none()
        if donation:
            donation.status = "cancelled"
            await db.commit()

    return {"status": "ok"}
