"""
kiln_shared.rate_limit
----------------------
Per-user rate limiting via slowapi.

The key function — ``kiln_user_key()`` — returns the authenticated user
ID when present (Clerk JWT or API key) and falls back to the client IP
for unauthenticated routes (e.g. /health, /readyz). This means a free
tier of one user can't accidentally rate-limit a paying tier of another
user when they share the same egress NAT IP.

Usage in service main.py:

    from fastapi import FastAPI
    from slowapi.errors import RateLimitExceeded
    from kiln_shared.rate_limit import (
        get_limiter,
        kiln_rate_limit_exceeded_handler,
    )

    app = FastAPI()
    limiter = get_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, kiln_rate_limit_exceeded_handler)

    @app.post("/expensive")
    @limiter.limit("30/minute")
    async def expensive(request: Request, user = Depends(require_auth)):
        ...

Limits are configurable via env vars:
    KILN_RATE_LIMIT_DEFAULT      (default: "1000/minute")
    KILN_RATE_LIMIT_STORAGE_URI  (default: in-memory; set to redis://... in prod)

For production deploys with multiple replicas, set ``KILN_RATE_LIMIT_STORAGE_URI``
to a shared Redis URL so the counters are coordinated across instances.
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from kiln_shared.request_id import current_request_id


def kiln_user_key(request: Request) -> str:
    """Rate-limit key: prefer authenticated user_id, fall back to remote IP.

    The auth middleware (or ``require_auth`` dependency) sets
    ``request.state.user`` to a ``KilnUser`` for authenticated routes. We
    read it directly from request.state instead of re-running the auth
    dependency so unauthenticated routes (health probes, etc) still get
    a sensible default.
    """
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "user_id", None):
        return f"user:{user.user_id}"
    return f"ip:{get_remote_address(request)}"


@lru_cache(maxsize=1)
def get_limiter() -> Limiter:
    """Build (or return the cached) Limiter for this process.

    Memoised because slowapi keeps per-instance state for in-memory
    storage; constructing a new Limiter on every import would lose the
    counters.
    """
    default_limit = os.environ.get("KILN_RATE_LIMIT_DEFAULT", "1000/minute")
    storage_uri = os.environ.get("KILN_RATE_LIMIT_STORAGE_URI", "memory://")
    return Limiter(
        key_func=kiln_user_key,
        default_limits=[default_limit],
        storage_uri=storage_uri,
        strategy="fixed-window",
    )


async def kiln_rate_limit_exceeded_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Friendly 429 response with Retry-After + the request ID for support.

    Annotated as ``Exception`` (the parameter type Starlette's
    ``add_exception_handler`` expects) but only ever invoked for
    ``RateLimitExceeded``. We narrow inside the body.
    """
    if not isinstance(exc, RateLimitExceeded):
        # Belt-and-suspenders: should never happen because we register
        # the handler against RateLimitExceeded specifically.
        return JSONResponse(status_code=500, content={"detail": str(exc)})
    retry_after = getattr(exc, "retry_after", None) or "60"
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded. Please wait and try again.",
            "limit": str(exc.detail),
            "request_id": current_request_id(),
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(exc.detail),
        },
    )
