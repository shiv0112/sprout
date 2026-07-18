"""
sprout_shared.cache
─────────────────
Redis caching for Sprout services.

Falls back gracefully when Redis is not available (local dev without Docker).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_redis_client = None
_redis_available = False


def _get_redis():
    """Lazy-initialize Redis connection."""
    global _redis_client, _redis_available  # noqa: PLW0603

    if _redis_client is not None:
        return _redis_client if _redis_available else None

    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        _redis_available = False
        _redis_client = False  # sentinel: tried but no URL
        return None

    try:
        import redis
        _redis_client = redis.from_url(redis_url, decode_responses=True)
        _redis_client.ping()
        _redis_available = True
        logger.info("Redis connected: %s", redis_url)
        return _redis_client
    except Exception as e:
        logger.warning("Redis not available (%s), caching disabled", e)
        _redis_available = False
        _redis_client = False
        return None


async def cache_get(key: str) -> Any | None:
    """Get a value from Redis cache. Returns None on miss or if Redis unavailable."""
    r = _get_redis()
    if r is None:
        return None
    try:
        val = r.get(key)
        return json.loads(val) if val else None
    except Exception:
        return None


async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    """Set a value in Redis cache with TTL (seconds). No-op if Redis unavailable."""
    r = _get_redis()
    if r is None:
        return
    with contextlib.suppress(Exception):
        r.setex(key, ttl, json.dumps(value, default=str))


async def cache_delete(key: str) -> None:
    """Delete a key from Redis. No-op if Redis unavailable."""
    r = _get_redis()
    if r is None:
        return
    with contextlib.suppress(Exception):
        r.delete(key)


async def cache_invalidate_pattern(pattern: str) -> None:
    """Delete all keys matching a pattern. No-op if Redis unavailable."""
    r = _get_redis()
    if r is None:
        return
    try:
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
    except Exception:
        pass
