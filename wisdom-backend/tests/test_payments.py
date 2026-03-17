"""Tests for payments module -- invoices, webhooks, WhatsApp retry."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch
import hmac
import hashlib

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TestSessionLocal, test_engine
from app.database import Base, get_db
from app.payments.models import Invoice, Payment, Donation, DonationCampaign
from app.messaging.models import WhatsAppMessage, DeadLetterQueue


# ---------------------------------------------------------------------------
# Test-scoped app with payments + auth routers
# ---------------------------------------------------------------------------

def _create_payments_app():
    """Create a FastAPI app with payments and auth routers for tests."""
    from fastapi import FastAPI
    from app.auth.router import router as auth_router
    from app.payments.router import router as payments_router

    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1/auth")
    app.include_router(payments_router, prefix="/api/v1/payments")

    async def _override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    return app


_test_app = _create_payments_app()


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


# ===========================================================================
# INVOICE TESTS
# ===========================================================================


class TestInvoiceCreation:
    """Tests for invoice creation with line items and GST calculation."""

    @pytest.mark.asyncio
    async def test_create_invoice_with_line_items(
        self, client, auth_cookies, users
    ):
        """Create an invoice with 2 line items. Verify subtotal, GST (18%),
        and total are correctly calculated."""
        invoice_data = {
            "billing_name": "Dr. Sharma",
            "billing_email": "sharma@example.com",
            "billing_phone": "+91-9876543210",
            "line_items": [
                {
                    "item_type": "therapy_session",
                    "description": "Individual therapy session (1 hour)",
                    "rate": 2000.00,
                    "quantity": 4,
                },
                {
                    "item_type": "workshop",
                    "description": "Parent workshop attendance",
                    "rate": 1500.00,
                    "quantity": 1,
                },
            ],
            "discount": 0,
            "apply_gst": True,
            "currency": "INR",
        }
        resp = await client.post(
            "/api/v1/payments/invoices",
            json=invoice_data,
            cookies=auth_cookies,
        )
        assert resp.status_code == 201, f"Invoice creation failed: {resp.text}"

        body = resp.json()
        # 4 * 2000 + 1 * 1500 = 9500 subtotal
        assert body["subtotal"] == 9500.0, f"Expected subtotal 9500.0, got {body['subtotal']}"
        # GST 18% of 9500 = 1710
        assert body["tax_amount"] == 1710.0, f"Expected tax 1710.0, got {body['tax_amount']}"
        # Total = 9500 + 1710 = 11210
        assert body["total"] == 11210.0, f"Expected total 11210.0, got {body['total']}"
        assert body["discount_amount"] == 0.0
        assert body["currency"] == "INR"
        assert body["status"] == "draft"
        assert body["invoice_number"].startswith("INV-")


# ===========================================================================
# RAZORPAY WEBHOOK TESTS
# ===========================================================================


class TestRazorpayWebhook:
    """Tests for Razorpay webhook handling."""

    @pytest.mark.asyncio
    async def test_razorpay_webhook_payment_captured(
        self, client, db: AsyncSession, users
    ):
        """Simulate a Razorpay webhook payload for payment.captured. Verify
        invoice status becomes 'paid' and Payment record has status='captured'."""
        # Create an invoice directly in the DB to be matched by the webhook
        invoice = Invoice(
            invoice_number="INV-2026-99001",
            billing_name="Webhook Test",
            subtotal=5000,
            discount_amount=0,
            tax_amount=900,
            total=5900,
            currency="INR",
            status="sent",
        )
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)

        gateway_payment_id = f"pay_test_{uuid4().hex[:12]}"

        # Build the webhook payload
        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": gateway_payment_id,
                        "amount": 590000,  # paise
                        "currency": "INR",
                        "method": "upi",
                        "order_id": "order_test_123",
                        "notes": {
                            "invoice_number": "INV-2026-99001",
                        },
                    }
                }
            },
        }

        # Compute signature using the test secret
        with patch("app.payments.razorpay.get_settings") as mock_settings:
            test_secret = "test_razorpay_secret"
            mock_settings.return_value = MagicMock(
                RAZORPAY_KEY_ID="test_key_id",
                RAZORPAY_KEY_SECRET=test_secret,
            )

            webhook_body = str(payload).encode()
            valid_signature = hmac.new(
                test_secret.encode(),
                webhook_body,
                hashlib.sha256,
            ).hexdigest()

            resp = await client.post(
                "/api/v1/payments/razorpay/webhook",
                json=payload,
                headers={"X-Razorpay-Signature": valid_signature},
            )

        assert resp.status_code == 200, f"Webhook failed: {resp.text}"
        assert resp.json()["status"] == "ok"

        # Use a fresh session to see changes committed by the webhook handler
        async with TestSessionLocal() as verify_db:
            pay_result = await verify_db.execute(
                select(Payment).where(Payment.gateway_payment_id == gateway_payment_id)
            )
            payment = pay_result.scalar_one_or_none()
            assert payment is not None, "Payment record should be created"
            assert payment.status == "captured"
            assert payment.gateway == "razorpay"
            assert payment.method == "upi"

            inv_result = await verify_db.execute(
                select(Invoice).where(Invoice.id == invoice.id)
            )
            updated_invoice = inv_result.scalar_one_or_none()
            assert updated_invoice is not None
            assert updated_invoice.status == "paid"
            assert updated_invoice.paid_at is not None

    @pytest.mark.asyncio
    async def test_razorpay_webhook_invalid_signature(self, client, users):
        """Send a webhook with wrong signature. Expect 400 rejection."""
        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_fake",
                        "amount": 100000,
                        "currency": "INR",
                        "method": "card",
                    }
                }
            },
        }

        with patch("app.payments.razorpay.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                RAZORPAY_KEY_ID="test_key_id",
                RAZORPAY_KEY_SECRET="real_secret",
            )

            # Send with a bad signature
            resp = await client.post(
                "/api/v1/payments/razorpay/webhook",
                json=payload,
                headers={"X-Razorpay-Signature": "invalid_signature_abc123"},
            )

        assert resp.status_code == 400, (
            f"Expected 400 for invalid signature, got {resp.status_code}: {resp.text}"
        )


# ===========================================================================
# STRIPE WEBHOOK TESTS
# ===========================================================================


class TestStripeWebhook:
    """Tests for Stripe webhook handling."""

    @pytest.mark.asyncio
    async def test_stripe_webhook_payment_succeeded(
        self, client, db: AsyncSession, users
    ):
        """Simulate a Stripe webhook payload for payment_intent.succeeded.
        Verify donation status is updated."""
        # Create a donation and campaign in the DB
        campaign = DonationCampaign(
            title="Test Campaign",
            description="For testing",
            goal_amount=100000,
            raised_amount=0,
            is_active=True,
        )
        db.add(campaign)
        await db.commit()
        await db.refresh(campaign)

        donation = Donation(
            donor_name="Test Donor",
            donor_email="donor@example.com",
            amount=5000,
            currency="USD",
            campaign_id=campaign.id,
            status="pending",
        )
        db.add(donation)
        await db.commit()
        await db.refresh(donation)

        gateway_payment_id = f"pi_test_{uuid4().hex[:12]}"

        # Mock stripe.Webhook.construct_event to return a fake event
        mock_event = MagicMock()
        mock_event.type = "payment_intent.succeeded"
        mock_event.data.object.id = gateway_payment_id
        mock_event.data.object.amount = 500000  # cents
        mock_event.data.object.currency = "usd"
        mock_event.data.object.metadata = {"donation_id": str(donation.id)}

        with patch("app.payments.stripe_client.stripe.Webhook.construct_event", return_value=mock_event), \
             patch("app.payments.stripe_client.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                STRIPE_SECRET_KEY="sk_test_xxx",
                STRIPE_WEBHOOK_SECRET="whsec_test",
            )

            resp = await client.post(
                "/api/v1/payments/stripe/webhook",
                content=b'{"type": "payment_intent.succeeded"}',
                headers={
                    "Stripe-Signature": "t=123,v1=abc",
                    "Content-Type": "application/json",
                },
            )

        assert resp.status_code == 200, f"Stripe webhook failed: {resp.text}"
        assert resp.json()["status"] == "ok"

        # Use a fresh session to see changes committed by the webhook handler
        async with TestSessionLocal() as verify_db:
            pay_result = await verify_db.execute(
                select(Payment).where(Payment.gateway_payment_id == gateway_payment_id)
            )
            payment = pay_result.scalar_one_or_none()
            assert payment is not None, "Payment record should be created"
            assert payment.status == "captured"
            assert payment.gateway == "stripe"

            don_result = await verify_db.execute(
                select(Donation).where(Donation.id == donation.id)
            )
            updated_donation = don_result.scalar_one_or_none()
            assert updated_donation is not None
            assert updated_donation.status == "captured"

            camp_result = await verify_db.execute(
                select(DonationCampaign).where(DonationCampaign.id == campaign.id)
            )
            updated_campaign = camp_result.scalar_one_or_none()
            assert float(updated_campaign.raised_amount) == 5000.0


# ===========================================================================
# WHATSAPP DEAD LETTER TESTS
# ===========================================================================


class TestWhatsAppDeadLetter:
    """Tests for WhatsApp send failure and dead letter queue."""

    @pytest.mark.asyncio
    async def test_whatsapp_send_failure_dead_letter(self, db: AsyncSession):
        """Test that when WhatsApp send fails after 3 retries, a
        DeadLetterQueue entry is created."""
        from app.messaging.whatsapp import send_whatsapp_template

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.messaging.whatsapp.get_settings") as mock_settings, \
             patch("app.messaging.whatsapp.httpx.AsyncClient", return_value=mock_client), \
             patch("app.messaging.whatsapp.asyncio.sleep", new_callable=AsyncMock), \
             patch("app.messaging.whatsapp.sentry_sdk"):

            mock_settings.return_value = MagicMock(
                WHATSAPP_TOKEN="test-token-active",
                WHATSAPP_PHONE_ID="12345",
            )

            msg = await send_whatsapp_template(
                recipient_phone="+91-8888888888",
                template_name="RISK_ALERT_P0",
                template_params=["ARK-DEAD-001", "Test Assessment", "Q1", "2026-03-16"],
                case_id=None,
                risk_alert_id=None,
                db=db,
                max_retries=3,
            )

        # Verify message status is failed
        assert msg.status == "failed", f"Expected status 'failed', got '{msg.status}'"

        # Verify the post was called 3 times (3 retries)
        assert mock_client.post.call_count == 3, (
            f"Expected 3 retry attempts, got {mock_client.post.call_count}"
        )

        # Verify a DeadLetterQueue entry was created
        dlq_result = await db.execute(
            select(DeadLetterQueue).where(
                DeadLetterQueue.service == "whatsapp",
            )
        )
        dlq_entries = dlq_result.scalars().all()
        # Find the entry matching our message
        matching = [
            e for e in dlq_entries
            if e.payload and e.payload.get("message_id") == str(msg.id)
        ]
        assert len(matching) >= 1, (
            "DeadLetterQueue entry should be created after retry exhaustion"
        )
        entry = matching[0]
        assert entry.resolved is False
        assert "Connection refused" in entry.error_message
        assert entry.payload["template_name"] == "RISK_ALERT_P0"
        assert entry.payload["recipient_phone"] == "+91-8888888888"
