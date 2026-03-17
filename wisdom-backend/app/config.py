"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://wisdom:wisdom@localhost:5432/wisdom_db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Security
    SECRET_KEY: str = "change-me-in-production"

    # Sentry
    SENTRY_DSN: str = ""

    # Frontend
    FRONTEND_URL: str = "http://localhost:3000"

    # WhatsApp
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_PHONE_ID: str = ""

    # Razorpay
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # LiteLLM / AI
    LITELLM_API_KEY: str = ""
    LITELLM_MODEL: str = "gpt-4o"

    # Jina AI (Embeddings)
    JINA_API_KEY: str = ""

    # Encryption
    ENCRYPTION_KEY: str = ""  # AES-256, 32 bytes, base64-encoded

    # Environment
    ENVIRONMENT: str = "development"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
