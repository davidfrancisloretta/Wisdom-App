"""Async Redis client and helpers."""

import json
from typing import Any, Optional

import redis.asyncio as redis

from app.config import get_settings

settings = get_settings()

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


async def get_redis() -> redis.Redis:
    return redis_client


async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    await redis_client.set(key, json.dumps(value), ex=ttl)


async def cache_get(key: str) -> Optional[Any]:
    data = await redis_client.get(key)
    if data is not None:
        return json.loads(data)
    return None


async def cache_delete(key: str) -> None:
    await redis_client.delete(key)
