"""WISDOM APP — FastAPI Application Entry Point."""

# Sentry must be initialised before any other import
from app.sentry_init import init_sentry

init_sentry()

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal, engine, get_db
from app.redis_client import redis_client

logger = logging.getLogger(__name__)

# Domain routers
from app.auth.router import router as auth_router
from app.cases.router import router as cases_router
from app.assessments.router import router as assessments_router, parent_router as parent_assessments_router
from app.scheduling.router import router as scheduling_router
from app.payments.router import router as payments_router
from app.donations.router import router as donations_router
from app.analytics.router import router as analytics_router
from app.public.router import router as public_router
from app.admin.router import router as admin_router
from app.ai.router import router as ai_router

settings = get_settings()


async def _notification_processor():
    """Background loop: process pending scheduled notifications every 5 minutes."""
    from app.messaging.notifications import process_scheduled_notifications

    while True:
        try:
            async with AsyncSessionLocal() as db:
                await process_scheduled_notifications(db)
        except Exception:
            logger.exception("Error processing scheduled notifications")
        await asyncio.sleep(300)  # 5 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify DB and Redis connections
    async with engine.connect() as conn:
        await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    await redis_client.ping()

    # Start notification processor background task
    task = asyncio.create_task(_notification_processor())
    yield
    # Shutdown
    task.cancel()
    await engine.dispose()
    await redis_client.close()


app = FastAPI(
    title="WISDOM APP API",
    version="1.0.0",
    description="Wellness & Integrated Support for Development, Observation & Mentoring — The Ark Centre",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all domain routers under /api/v1/
app.include_router(auth_router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(cases_router, prefix="/api/v1/cases", tags=["Cases"])
app.include_router(assessments_router, prefix="/api/v1/assessments", tags=["Assessments"])
app.include_router(scheduling_router, prefix="/api/v1/scheduling", tags=["Scheduling"])
app.include_router(payments_router, prefix="/api/v1/payments", tags=["Payments"])
app.include_router(donations_router, prefix="/api/v1/donations", tags=["Donations"])
app.include_router(analytics_router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(public_router, prefix="/api/v1/public", tags=["Public"])
app.include_router(parent_assessments_router, prefix="/api/v1/parent", tags=["Parent"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(ai_router, prefix="/api/v1/ai", tags=["AI"])


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/v1/sentry-test")
async def sentry_test():
    """Deliberately raises an exception to test Sentry integration."""
    raise RuntimeError("Sentry test — this error should appear in the Sentry dashboard")


@app.post("/api/v1/messaging/test")
async def test_whatsapp(
    phone: str = "919999999999",
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
):
    """Test endpoint to send a WhatsApp template message."""
    from app.messaging.whatsapp import send_whatsapp_template

    msg = await send_whatsapp_template(
        recipient_phone=phone,
        template_name="RISK_ALERT_P0",
        template_params=["TEST-001", "Test Assessment", "Test question", "2026-03-16T00:00:00"],
        case_id=None,
        risk_alert_id=None,
        db=db,
    )
    return {
        "id": str(msg.id),
        "status": msg.status,
        "whatsapp_message_id": msg.whatsapp_message_id,
    }
