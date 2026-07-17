"""Tests for the per-user rate limiter helpers.

Pin the contract:
  - Authenticated requests are keyed by user_id (so two users on the
    same NAT IP don't interfere)
  - Unauthenticated requests fall back to client IP
  - The handler returns 429 with Retry-After + the request ID
  - The full integration with FastAPI rejects the N+1 request
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from kiln_shared.rate_limit import (
    get_limiter,
    kiln_rate_limit_exceeded_handler,
    kiln_user_key,
)


@dataclass
class _StubUser:
    user_id: str
    email: str = ""
    name: str = ""


def _request_with_user(user: _StubUser | None) -> Request:
    """Build a minimal Request object with optional user state."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": ("198.51.100.7", 12345),
    }
    req = Request(scope)
    if user is not None:
        req.state.user = user
    return req


def test_kiln_user_key_uses_user_id_when_authenticated() -> None:
    req = _request_with_user(_StubUser(user_id="clerk_user_abc"))
    assert kiln_user_key(req) == "user:clerk_user_abc"


def test_kiln_user_key_falls_back_to_ip_when_anonymous() -> None:
    req = _request_with_user(None)
    key = kiln_user_key(req)
    assert key.startswith("ip:")
    assert "198.51.100.7" in key


def test_get_limiter_is_memoised() -> None:
    a = get_limiter()
    b = get_limiter()
    assert a is b


@pytest.mark.asyncio
async def test_handler_widens_unknown_exception_to_500() -> None:
    """Belt-and-suspenders: a non-RateLimitExceeded exception passes through
    as a 500 rather than crashing the handler. The Starlette type system
    forces us to declare the param as Exception, so this branch matters."""
    req = _request_with_user(None)
    resp = await kiln_rate_limit_exceeded_handler(req, ValueError("boom"))
    assert resp.status_code == 500


def test_integration_route_blocks_after_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """A route limited to 2/minute should accept 2 requests then 429."""
    # Reset the memoised limiter so the env var override takes effect
    get_limiter.cache_clear()
    monkeypatch.setenv("KILN_RATE_LIMIT_DEFAULT", "2/minute")
    monkeypatch.setenv("KILN_RATE_LIMIT_STORAGE_URI", "memory://")

    limiter = get_limiter()

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, kiln_rate_limit_exceeded_handler)

    @app.get("/limited")
    @limiter.limit("2/minute")
    async def limited(request: Request) -> dict:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200
    blocked = client.get("/limited")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers

    # Reset for other tests
    get_limiter.cache_clear()
