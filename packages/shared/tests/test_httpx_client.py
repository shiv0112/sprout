"""Tests for the request-id-aware httpx client factory.

Pin the contract:
  - The factory adds X-Sprout-Request-ID to outbound requests automatically
  - The current contextvar value is used when set
  - A fresh UUID is minted when no contextvar is active (background tasks)
  - A caller-supplied X-Sprout-Request-ID header is preserved (not overwritten)
"""

from __future__ import annotations

import httpx
import pytest

from sprout_shared.httpx_client import async_client
from sprout_shared.request_id import REQUEST_ID_HEADER, set_request_id


def _make_transport() -> httpx.MockTransport:
    """Mock transport that echoes the request-id header back as JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"received_rid": request.headers.get(REQUEST_ID_HEADER, "")},
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_outbound_request_carries_active_request_id() -> None:
    set_request_id("ctx-fixture-abc")
    try:
        async with async_client(transport=_make_transport()) as client:
            resp = await client.get("http://internal/whatever")
        assert resp.json()["received_rid"] == "ctx-fixture-abc"
    finally:
        set_request_id("-")


@pytest.mark.asyncio
async def test_outbound_request_mints_fresh_uuid_outside_request() -> None:
    # Default contextvar is "-" so the factory should mint a real UUID.
    set_request_id("-")
    async with async_client(transport=_make_transport()) as client:
        resp = await client.get("http://internal/whatever")
    rid = resp.json()["received_rid"]
    assert len(rid) == 32  # uuid4 hex
    assert rid != "-"


@pytest.mark.asyncio
async def test_caller_supplied_header_is_preserved() -> None:
    """Explicit per-call header beats both the contextvar and the auto-mint."""
    set_request_id("ctx-fixture-xyz")
    try:
        async with async_client(transport=_make_transport()) as client:
            resp = await client.get(
                "http://internal/whatever",
                headers={REQUEST_ID_HEADER: "explicit-override"},
            )
        assert resp.json()["received_rid"] == "explicit-override"
    finally:
        set_request_id("-")
