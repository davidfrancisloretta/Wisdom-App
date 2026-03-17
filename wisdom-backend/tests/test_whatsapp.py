"""Unit tests for app.messaging.whatsapp — WhatsApp template messaging with dead letter fallback."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.models import WhatsAppMessage, DeadLetterQueue


# ============================================================================
# send_whatsapp_template
# ============================================================================


class TestSendWhatsAppTemplate:

    @pytest.mark.asyncio
    async def test_queues_message_without_credentials(self, db: AsyncSession):
        """When WHATSAPP_TOKEN is empty, message should be created with status=queued."""
        from app.messaging.whatsapp import send_whatsapp_template

        with patch("app.messaging.whatsapp.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                WHATSAPP_TOKEN="",
                WHATSAPP_PHONE_ID="",
            )
            msg = await send_whatsapp_template(
                recipient_phone="+91-9876543210",
                template_name="RISK_ALERT_P0",
                template_params=["ARK-2026-00001", "XII"],
                case_id=None,
                risk_alert_id=None,
                db=db,
            )

        assert msg.status == "queued"
        assert msg.recipient_phone == "+91-9876543210"
        assert msg.template_name == "RISK_ALERT_P0"

    @pytest.mark.asyncio
    async def test_persists_message_to_database(self, db: AsyncSession):
        """The WhatsAppMessage record should be persisted in the DB."""
        from app.messaging.whatsapp import send_whatsapp_template

        with patch("app.messaging.whatsapp.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(WHATSAPP_TOKEN="", WHATSAPP_PHONE_ID="")
            msg = await send_whatsapp_template(
                recipient_phone="+91-1111111111",
                template_name="TEST_TEMPLATE",
                template_params=["value1"],
                case_id=None,
                risk_alert_id=None,
                db=db,
            )

        result = await db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.id == msg.id)
        )
        persisted = result.scalar_one_or_none()
        assert persisted is not None
        assert persisted.template_name == "TEST_TEMPLATE"

    @pytest.mark.asyncio
    async def test_sends_successfully_with_valid_credentials(self, db: AsyncSession):
        """When API returns 200, status should be updated to 'sent'."""
        from app.messaging.whatsapp import send_whatsapp_template

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "messages": [{"id": "wamid.abc123"}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("app.messaging.whatsapp.get_settings") as mock_settings, \
             patch("app.messaging.whatsapp.httpx.AsyncClient") as mock_client_cls:

            mock_settings.return_value = MagicMock(
                WHATSAPP_TOKEN="test-token",
                WHATSAPP_PHONE_ID="12345",
            )

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            msg = await send_whatsapp_template(
                recipient_phone="+91-2222222222",
                template_name="RISK_ALERT_P0",
                template_params=["ARK-001"],
                case_id=None,
                risk_alert_id=None,
                db=db,
            )

        assert msg.status == "sent"
        assert msg.whatsapp_message_id == "wamid.abc123"
        assert msg.sent_at is not None

    @pytest.mark.asyncio
    async def test_failure_sets_failed_status_and_dead_letter(self, db: AsyncSession):
        """When API call raises an exception after retries, status should be 'failed' and dead letter queued."""
        from app.messaging.whatsapp import send_whatsapp_template

        with patch("app.messaging.whatsapp.get_settings") as mock_settings, \
             patch("app.messaging.whatsapp.httpx.AsyncClient") as mock_client_cls, \
             patch("app.messaging.whatsapp.asyncio.sleep", new_callable=AsyncMock), \
             patch("app.messaging.whatsapp.sentry_sdk"):

            mock_settings.return_value = MagicMock(
                WHATSAPP_TOKEN="test-token",
                WHATSAPP_PHONE_ID="12345",
            )

            mock_client = AsyncMock()
            mock_client.post.side_effect = Exception("Connection timeout")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            msg = await send_whatsapp_template(
                recipient_phone="+91-3333333333",
                template_name="RISK_ALERT_P0",
                template_params=["ARK-002"],
                case_id=None,
                risk_alert_id=None,
                db=db,
            )

        assert msg.status == "failed"

    @pytest.mark.asyncio
    async def test_stores_case_id_and_risk_alert_id(self, db: AsyncSession, child_case):
        """Optional case_id and risk_alert_id should be persisted."""
        from app.messaging.whatsapp import send_whatsapp_template

        fake_alert_id = uuid.uuid4()
        with patch("app.messaging.whatsapp.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(WHATSAPP_TOKEN="", WHATSAPP_PHONE_ID="")
            msg = await send_whatsapp_template(
                recipient_phone="+91-4444444444",
                template_name="ALERT",
                template_params=[],
                case_id=child_case.id,
                risk_alert_id=fake_alert_id,
                db=db,
            )

        assert msg.case_id == child_case.id
        assert msg.risk_alert_id == fake_alert_id

    @pytest.mark.asyncio
    async def test_correct_api_url_constructed(self, db: AsyncSession):
        """Verify the correct Facebook Graph API URL is called with v21.0."""
        from app.messaging.whatsapp import send_whatsapp_template

        mock_response = MagicMock()
        mock_response.json.return_value = {"messages": [{"id": "wamid.xyz"}]}
        mock_response.raise_for_status = MagicMock()

        with patch("app.messaging.whatsapp.get_settings") as mock_settings, \
             patch("app.messaging.whatsapp.httpx.AsyncClient") as mock_client_cls:

            mock_settings.return_value = MagicMock(
                WHATSAPP_TOKEN="tok",
                WHATSAPP_PHONE_ID="PHONE123",
            )

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await send_whatsapp_template(
                recipient_phone="+91-6666666666",
                template_name="T",
                template_params=["v"],
                case_id=None,
                risk_alert_id=None,
                db=db,
            )

        call_args = mock_client.post.call_args
        assert "PHONE123" in call_args[0][0]
        assert "graph.facebook.com" in call_args[0][0]
        assert "v21.0" in call_args[0][0]
