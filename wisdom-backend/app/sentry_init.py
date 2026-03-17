"""Sentry SDK initialisation — must be called before any other import in main.py."""

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.config import get_settings


def init_sentry() -> None:
    settings = get_settings()
    if not settings.SENTRY_DSN:
        return

    traces_sample_rate = 0.2 if settings.ENVIRONMENT == "production" else 1.0

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=traces_sample_rate,
        environment=settings.ENVIRONMENT,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
        ],
    )
