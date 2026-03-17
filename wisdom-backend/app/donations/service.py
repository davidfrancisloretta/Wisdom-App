"""Donation service — campaign and donation business logic."""

import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import BackgroundTasks
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.donations.models import Donation, DonationCampaign
from app.messaging.whatsapp import send_whatsapp_template

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _campaign_to_dict(campaign, donor_count: int = 0) -> dict:
    """Convert a DonationCampaign ORM object to a plain dict (avoids greenlet issues)."""
    goal = float(campaign.goal_amount) if campaign.goal_amount else 0
    raised = float(campaign.raised_amount) if campaign.raised_amount else 0
    progress = (raised / goal * 100) if goal > 0 else 0
    return {
        "id": campaign.id,
        "title": campaign.title,
        "description": campaign.description,
        "goal_amount": goal,
        "raised_amount": raised,
        "is_active": campaign.is_active,
        "room_id": campaign.room_id,
        "start_date": campaign.start_date,
        "end_date": campaign.end_date,
        "created_by": campaign.created_by,
        "donor_count": donor_count,
        "progress_pct": round(progress, 1),
    }


def _donation_to_dict(donation, campaign_title: Optional[str] = None) -> dict:
    """Convert a Donation ORM object to a plain dict."""
    return {
        "id": donation.id,
        "donor_name": donation.donor_name,
        "donor_email": donation.donor_email,
        "donor_phone": donation.donor_phone,
        "campaign_id": donation.campaign_id,
        "amount": float(donation.amount),
        "currency": donation.currency,
        "is_recurring": donation.is_recurring,
        "recurrence_interval": donation.recurrence_interval,
        "status": donation.status,
        "receipt_sent": donation.receipt_sent,
        "created_at": donation.created_at,
        "campaign_title": campaign_title,
    }


# ---------------------------------------------------------------------------
# Razorpay helpers
# ---------------------------------------------------------------------------

async def _create_razorpay_order(amount_inr: float, receipt: str) -> dict:
    """Create a Razorpay order. Amount in INR is converted to paise."""
    settings = get_settings()
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        logger.warning("Razorpay credentials not configured — returning stub order")
        return {"id": f"order_stub_{uuid.uuid4().hex[:12]}", "amount": int(amount_inr * 100), "currency": "INR"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.razorpay.com/v1/orders",
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            json={
                "amount": int(amount_inr * 100),  # paise
                "currency": "INR",
                "receipt": receipt,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _create_razorpay_subscription(plan_amount: float, interval: str, donor_email: str) -> dict:
    """Create a Razorpay subscription for recurring donations."""
    settings = get_settings()
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        logger.warning("Razorpay credentials not configured — returning stub subscription")
        return {"id": f"sub_stub_{uuid.uuid4().hex[:12]}", "plan_id": "plan_stub", "status": "created"}

    period_map = {"monthly": "monthly", "quarterly": "monthly", "annually": "yearly"}
    interval_count_map = {"monthly": 1, "quarterly": 3, "annually": 1}
    period = period_map.get(interval, "monthly")
    interval_count = interval_count_map.get(interval, 1)

    async with httpx.AsyncClient(timeout=30) as client:
        # Create a plan first
        plan_resp = await client.post(
            "https://api.razorpay.com/v1/plans",
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            json={
                "period": period,
                "interval": interval_count,
                "item": {
                    "name": f"Donation {interval}",
                    "amount": int(plan_amount * 100),
                    "currency": "INR",
                },
            },
        )
        plan_resp.raise_for_status()
        plan = plan_resp.json()

        # Create subscription
        sub_resp = await client.post(
            "https://api.razorpay.com/v1/subscriptions",
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            json={
                "plan_id": plan["id"],
                "total_count": 120,
                "notify_info": {"notify_email": donor_email},
            },
        )
        sub_resp.raise_for_status()
        return sub_resp.json()


# ---------------------------------------------------------------------------
# Stripe helpers
# ---------------------------------------------------------------------------

async def _create_stripe_payment_intent(amount: float, currency: str) -> dict:
    """Create a Stripe PaymentIntent for international donations."""
    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        logger.warning("Stripe credentials not configured — returning stub intent")
        return {"id": f"pi_stub_{uuid.uuid4().hex[:12]}", "client_secret": "stub_secret", "status": "requires_payment_method"}

    # Stripe expects amount in smallest currency unit (cents for USD, etc.)
    smallest_unit = int(amount * 100)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.stripe.com/v1/payment_intents",
            headers={"Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}"},
            data={
                "amount": smallest_unit,
                "currency": currency.lower(),
                "payment_method_types[]": "card",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _create_stripe_subscription(amount: float, currency: str, interval: str, donor_email: str) -> dict:
    """Create a Stripe subscription for international recurring donations."""
    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        logger.warning("Stripe credentials not configured — returning stub subscription")
        return {"id": f"sub_stub_{uuid.uuid4().hex[:12]}", "status": "incomplete", "client_secret": "stub_secret"}

    stripe_interval_map = {"monthly": "month", "quarterly": "month", "annually": "year"}
    stripe_interval_count = {"monthly": 1, "quarterly": 3, "annually": 1}
    stripe_int = stripe_interval_map.get(interval, "month")
    stripe_count = stripe_interval_count.get(interval, 1)

    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}"}

        # Create or find customer
        cust_resp = await client.post(
            "https://api.stripe.com/v1/customers",
            headers=headers,
            data={"email": donor_email},
        )
        cust_resp.raise_for_status()
        customer = cust_resp.json()

        # Create price
        price_resp = await client.post(
            "https://api.stripe.com/v1/prices",
            headers=headers,
            data={
                "unit_amount": int(amount * 100),
                "currency": currency.lower(),
                "recurring[interval]": stripe_int,
                "recurring[interval_count]": stripe_count,
                "product_data[name]": f"Donation {interval}",
            },
        )
        price_resp.raise_for_status()
        price = price_resp.json()

        # Create subscription
        sub_resp = await client.post(
            "https://api.stripe.com/v1/subscriptions",
            headers=headers,
            data={
                "customer": customer["id"],
                "items[0][price]": price["id"],
                "payment_behavior": "default_incomplete",
                "expand[]": "latest_invoice.payment_intent",
            },
        )
        sub_resp.raise_for_status()
        return sub_resp.json()


# ---------------------------------------------------------------------------
# Campaign CRUD
# ---------------------------------------------------------------------------

async def list_campaigns(db: AsyncSession, active_only: bool = True) -> list[dict]:
    """List campaigns with donor count and progress percentage."""
    query = select(DonationCampaign)
    if active_only:
        query = query.where(DonationCampaign.is_active.is_(True))
    query = query.order_by(DonationCampaign.start_date.desc().nullslast())

    result = await db.execute(query)
    campaigns = result.scalars().all()

    items = []
    for campaign in campaigns:
        count_q = select(func.count(Donation.id)).where(
            Donation.campaign_id == campaign.id,
            Donation.status == "captured",
        )
        count_result = await db.execute(count_q)
        donor_count = count_result.scalar() or 0
        items.append(_campaign_to_dict(campaign, donor_count))

    return items


async def get_campaign(campaign_id: uuid.UUID, db: AsyncSession) -> dict | None:
    """Get a single campaign with donor count."""
    result = await db.execute(
        select(DonationCampaign).where(DonationCampaign.id == campaign_id)
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        return None

    count_result = await db.execute(
        select(func.count(Donation.id)).where(
            Donation.campaign_id == campaign.id,
            Donation.status == "captured",
        )
    )
    donor_count = count_result.scalar() or 0
    return _campaign_to_dict(campaign, donor_count)


async def create_campaign(
    data,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    """Create a new donation campaign."""
    campaign = DonationCampaign(
        title=data.title,
        description=data.description,
        goal_amount=data.goal_amount,
        room_id=data.room_id,
        start_date=data.start_date,
        end_date=data.end_date,
        created_by=user_id,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return _campaign_to_dict(campaign)


async def update_campaign(
    campaign_id: uuid.UUID,
    data,
    db: AsyncSession,
) -> dict | None:
    """Update campaign fields (only non-None values)."""
    result = await db.execute(
        select(DonationCampaign).where(DonationCampaign.id == campaign_id)
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        return None

    update_fields = data.model_dump(exclude_unset=True)
    for field, value in update_fields.items():
        setattr(campaign, field, value)

    await db.commit()
    await db.refresh(campaign)

    count_result = await db.execute(
        select(func.count(Donation.id)).where(
            Donation.campaign_id == campaign.id,
            Donation.status == "captured",
        )
    )
    donor_count = count_result.scalar() or 0
    return _campaign_to_dict(campaign, donor_count)


# ---------------------------------------------------------------------------
# One-time donation
# ---------------------------------------------------------------------------

async def create_one_time_donation(
    data,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Create a one-time donation record and initiate payment:
    - INR: create Razorpay order
    - International: create Stripe PaymentIntent
    Returns donation dict with gateway order/intent details.
    """
    donation = Donation(
        donor_name=data.donor_name,
        donor_email=data.donor_email,
        donor_phone=data.donor_phone,
        campaign_id=data.campaign_id,
        amount=data.amount,
        currency=data.currency,
        is_recurring=False,
        status="pending",
    )
    db.add(donation)
    await db.commit()
    await db.refresh(donation)

    # Determine campaign title
    campaign_title = None
    if donation.campaign_id:
        camp_result = await db.execute(
            select(DonationCampaign.title).where(DonationCampaign.id == donation.campaign_id)
        )
        campaign_title = camp_result.scalar_one_or_none()

    result = _donation_to_dict(donation, campaign_title)

    # Create payment gateway order/intent
    if data.currency.upper() == "INR":
        order = await _create_razorpay_order(data.amount, str(donation.id))
        donation.gateway = "razorpay"
        await db.commit()
        result["razorpay_order"] = order
    else:
        intent = await _create_stripe_payment_intent(data.amount, data.currency)
        donation.gateway = "stripe"
        await db.commit()
        result["stripe_intent"] = intent

    return result


# ---------------------------------------------------------------------------
# Recurring donation
# ---------------------------------------------------------------------------

async def create_recurring_donation(
    data,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Create a recurring donation record and initiate subscription:
    - INR: create Razorpay subscription
    - International: create Stripe subscription
    """
    donation = Donation(
        donor_name=data.donor_name,
        donor_email=data.donor_email,
        donor_phone=data.donor_phone,
        campaign_id=data.campaign_id,
        amount=data.amount,
        currency=data.currency,
        is_recurring=True,
        recurrence_interval=data.recurrence_interval,
        status="pending",
    )
    db.add(donation)
    await db.commit()
    await db.refresh(donation)

    campaign_title = None
    if donation.campaign_id:
        camp_result = await db.execute(
            select(DonationCampaign.title).where(DonationCampaign.id == donation.campaign_id)
        )
        campaign_title = camp_result.scalar_one_or_none()

    result = _donation_to_dict(donation, campaign_title)

    if data.currency.upper() == "INR":
        subscription = await _create_razorpay_subscription(data.amount, data.recurrence_interval, data.donor_email)
        donation.gateway = "razorpay"
        donation.gateway_subscription_id = subscription.get("id")
        await db.commit()
        result["razorpay_subscription"] = subscription
    else:
        subscription = await _create_stripe_subscription(
            data.amount, data.currency, data.recurrence_interval, data.donor_email,
        )
        donation.gateway = "stripe"
        donation.gateway_subscription_id = subscription.get("id")
        await db.commit()
        result["stripe_subscription"] = subscription

    return result


# ---------------------------------------------------------------------------
# Admin list
# ---------------------------------------------------------------------------

async def list_donations_admin(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 25,
    campaign_id: Optional[uuid.UUID] = None,
) -> dict:
    """Paginated list of all donations for admin view."""
    query = select(Donation).order_by(Donation.created_at.desc())
    count_query = select(func.count(Donation.id))

    if campaign_id:
        query = query.where(Donation.campaign_id == campaign_id)
        count_query = count_query.where(Donation.campaign_id == campaign_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    donations = result.scalars().all()

    # Collect campaign titles
    campaign_ids = {d.campaign_id for d in donations if d.campaign_id}
    campaign_titles: dict[uuid.UUID, str] = {}
    if campaign_ids:
        camp_result = await db.execute(
            select(DonationCampaign.id, DonationCampaign.title).where(
                DonationCampaign.id.in_(campaign_ids)
            )
        )
        for row in camp_result.all():
            campaign_titles[row[0]] = row[1]

    items = [
        _donation_to_dict(d, campaign_titles.get(d.campaign_id))
        for d in donations
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Receipt PDF generation
# ---------------------------------------------------------------------------

async def generate_receipt_pdf(donation_id: uuid.UUID, db: AsyncSession) -> bytes | None:
    """Generate a donation receipt PDF using reportlab. Returns PDF bytes or None."""
    result = await db.execute(
        select(Donation).where(Donation.id == donation_id)
    )
    donation = result.scalar_one_or_none()
    if not donation:
        return None

    campaign_title = "General Fund"
    if donation.campaign_id:
        camp_result = await db.execute(
            select(DonationCampaign.title).where(DonationCampaign.id == donation.campaign_id)
        )
        camp_title = camp_result.scalar_one_or_none()
        if camp_title:
            campaign_title = camp_title

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Header
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width / 2, height - 50 * mm, "Donation Receipt")

    c.setFont("Helvetica", 11)
    c.drawCentredString(width / 2, height - 60 * mm, "Wisdom Child Development Centre")

    # Divider
    c.line(30 * mm, height - 68 * mm, width - 30 * mm, height - 68 * mm)

    # Receipt details
    y = height - 80 * mm
    line_height = 7 * mm
    left = 35 * mm

    details = [
        ("Receipt No:", str(donation.id)[:8].upper()),
        ("Date:", donation.created_at.strftime("%d %B %Y") if donation.created_at else "N/A"),
        ("Donor Name:", donation.donor_name),
        ("Donor Email:", donation.donor_email or "N/A"),
        ("Donor Phone:", donation.donor_phone or "N/A"),
        ("Campaign:", campaign_title),
        ("Amount:", f"{donation.currency} {float(donation.amount):,.2f}"),
        ("Payment Status:", donation.status.capitalize()),
        ("Type:", "Recurring" if donation.is_recurring else "One-time"),
    ]

    if donation.is_recurring and donation.recurrence_interval:
        details.append(("Recurrence:", donation.recurrence_interval.capitalize()))

    for label, value in details:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(left, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(left + 40 * mm, y, value)
        y -= line_height

    # Footer
    y -= 15 * mm
    c.setFont("Helvetica-Oblique", 9)
    c.drawCentredString(width / 2, y, "Thank you for your generous contribution.")
    c.drawCentredString(width / 2, y - 5 * mm, "This receipt is auto-generated and does not require a signature.")

    c.save()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Handle donation captured (called from webhook)
# ---------------------------------------------------------------------------

async def handle_donation_captured(
    donation_id: uuid.UUID,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> dict | None:
    """
    Called after payment gateway confirms capture:
    1. Update donation status to 'captured'
    2. Update campaign raised_amount
    3. Generate receipt PDF
    4. Send WhatsApp DONATION_RECEIPT notification
    """
    result = await db.execute(
        select(Donation).where(Donation.id == donation_id)
    )
    donation = result.scalar_one_or_none()
    if not donation:
        return None

    # 1. Update status
    donation.status = "captured"
    await db.commit()
    await db.refresh(donation)

    # 2. Update campaign raised_amount
    campaign_title = "General Fund"
    if donation.campaign_id:
        camp_result = await db.execute(
            select(DonationCampaign).where(DonationCampaign.id == donation.campaign_id)
        )
        campaign = camp_result.scalar_one_or_none()
        if campaign:
            campaign_title = campaign.title
            campaign.raised_amount = float(campaign.raised_amount or 0) + float(donation.amount)
            await db.commit()

    # 3. Generate receipt
    receipt_bytes = await generate_receipt_pdf(donation_id, db)
    if receipt_bytes:
        donation.receipt_sent = True
        donation.receipt_sent_at = datetime.now(timezone.utc)
        await db.commit()

    # 4. Send WhatsApp receipt notification (in background)
    if donation.donor_phone:
        receipt_number = str(donation.id)[:8].upper()
        amount_str = f"{donation.currency} {float(donation.amount):,.2f}"

        async def _send_whatsapp():
            from app.database import AsyncSessionLocal
            async with AsyncSessionLocal() as wa_db:
                await send_whatsapp_template(
                    recipient_phone=donation.donor_phone,
                    template_name="DONATION_RECEIPT",
                    template_params=[donation.donor_name, amount_str, campaign_title, receipt_number],
                    case_id=None,
                    risk_alert_id=None,
                    db=wa_db,
                )

        background_tasks.add_task(_send_whatsapp)

    await db.refresh(donation)
    return _donation_to_dict(donation, campaign_title)
