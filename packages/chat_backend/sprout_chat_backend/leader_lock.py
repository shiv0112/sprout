"""Single-replica guard for chat_backend.

Chat sessions, SSE queues, and the in-memory DAG state all live on a
single process. Running two replicas at once silently splits traffic and
breaks streams. This module makes that misconfiguration loud:

    * On startup, the replica tries to ``SET NX`` a Redis key with a TTL.
    * A background task refreshes the TTL via a compare-and-set Lua
      script so it can only extend its *own* lock.
    * If another replica already holds the key on startup, the process
      fails fast with a clear log and ``sys.exit(1)`` so Kubernetes
      surfaces the error via CrashLoopBackOff.
    * If the heartbeat ever loses the lock (expired, evicted, or
      hijacked), the background task kills the process with
      ``os._exit(1)`` — we no longer satisfy the single-replica
      invariant, so it is unsafe to keep serving traffic.

Uses ``redis.asyncio`` so none of the Redis I/O blocks the event loop.
Gated by ``SPROUT_ENV``: skipped when ``SPROUT_ENV=dev`` so local dev and
tests don't require Redis. Hard-required otherwise.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

LEADER_KEY = "sprout:chat_backend:leader"
LEADER_TTL_SEC = 30
HEARTBEAT_INTERVAL_SEC = 10

# Atomic "refresh only if I still own it". Returns "OK" on success, nil if
# we have lost the lock (another replica, expiration, or eviction).
_REFRESH_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('set', KEYS[1], ARGV[1], 'EX', ARGV[2]) "
    "end return nil"
)

# Atomic "delete only if I still own it".
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) "
    "end return 0"
)


def _identity() -> str:
    return os.environ.get("POD_NAME") or socket.gethostname()


def _should_enforce() -> bool:
    env = os.environ.get("SPROUT_ENV", "dev").lower()
    if env == "dev":
        return False
    return os.environ.get("SPROUT_CHAT_LEADER_LOCK", "true").lower() not in {"false", "0", "no"}


async def _refresh(client, me: str):
    return await client.execute_command(
        "EVAL", _REFRESH_LUA, 1, LEADER_KEY, me, str(LEADER_TTL_SEC)
    )


async def _release(client, me: str):
    return await client.execute_command("EVAL", _RELEASE_LUA, 1, LEADER_KEY, me)


async def _acquire_or_exit(client, me: str) -> None:
    acquired = await client.set(LEADER_KEY, me, nx=True, ex=LEADER_TTL_SEC)
    if acquired:
        logger.info("Acquired chat_backend leader lock as %s", me)
        return

    holder = await client.get(LEADER_KEY) or "<unknown>"
    logger.error(
        "Another replica (%s) already holds the chat_backend leader lock. "
        "chat_backend is single-replica by design (in-memory SSE queues). "
        "Scale deployment replicas back to 1 or implement Redis-backed "
        "event queues before increasing replicas.",
        holder,
    )
    sys.exit(1)


async def _heartbeat(client, me: str, stop: asyncio.Event) -> None:
    """Refresh the leader lock. Kill the process if we ever lose it."""
    while not stop.is_set():
        lost = False
        try:
            result = await _refresh(client, me)
            if result is None:
                lost = True
        except Exception:
            logger.exception("leader-lock heartbeat call failed; retrying")

        if lost:
            logger.error(
                "chat_backend lost leader lock — another replica must have "
                "taken it or Redis evicted the key. Terminating to preserve "
                "single-replica invariant."
            )
            os._exit(1)

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SEC)


@asynccontextmanager
async def leader_lock_context():
    """Hold the leader lock for the lifetime of this context.

    No-op unless ``SPROUT_ENV`` is non-dev.
    """
    if not _should_enforce():
        yield
        return

    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        logger.error(
            "SPROUT_ENV=%s requires REDIS_URL for the chat_backend leader lock. "
            "Set REDIS_URL or set SPROUT_CHAT_LEADER_LOCK=false if you accept "
            "the single-replica footgun.",
            os.environ.get("SPROUT_ENV"),
        )
        sys.exit(1)

    try:
        from redis import asyncio as aioredis  # type: ignore[import-not-found]
    except ImportError:
        logger.error("redis package not installed; cannot acquire leader lock")
        sys.exit(1)

    client = aioredis.from_url(redis_url, decode_responses=True)
    me = _identity()
    await _acquire_or_exit(client, me)

    stop = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat(client, me, stop))

    try:
        yield
    finally:
        stop.set()
        try:
            await asyncio.wait_for(hb_task, timeout=2)
        except (TimeoutError, asyncio.CancelledError):
            hb_task.cancel()
        try:
            await _release(client, me)
        except Exception:
            logger.exception("failed to release leader lock on shutdown")
        with contextlib.suppress(Exception):
            await client.aclose()
