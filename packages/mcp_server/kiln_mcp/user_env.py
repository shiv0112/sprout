from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

_CACHE_TTL = 300
_cache: dict[str, tuple[dict[str, str], float]] = {}


async def fetch_user_env_vars(user_id: str) -> dict[str, str]:
    """Fetch the user's saved tool env vars from Clerk private_metadata.

    Cached for 5 minutes per user_id to avoid hammering the Clerk Backend API
    on every tool call.
    """
    now = time.time()
    cached = _cache.get(user_id)
    if cached is not None:
        data, expires_at = cached
        if now < expires_at:
            return data

    clerk_secret = os.environ.get("CLERK_SECRET_KEY", "").strip()
    if not clerk_secret:
        return {}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.clerk.com/v1/users/{user_id}",
                headers={"Authorization": f"Bearer {clerk_secret}"},
            )
            resp.raise_for_status()
            user_data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Failed to fetch tool env vars for user %s: %s", user_id, e)
        return {}

    env_vars = user_data.get("private_metadata", {}).get("tool_env_vars", {})
    _cache[user_id] = (env_vars, now + _CACHE_TTL)
    return env_vars
