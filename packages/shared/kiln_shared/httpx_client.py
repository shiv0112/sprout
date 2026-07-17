"""
kiln_shared.httpx_client
------------------------
httpx client factories that auto-attach the current Kiln request ID to
every outbound request, so the correlation chain set up by
``KilnRequestIDMiddleware`` extends across service boundaries.

Usage — replace this:

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("http://registry/tools")

with this:

    from kiln_shared.httpx_client import async_client

    async with async_client(timeout=10) as client:
        resp = await client.get("http://registry/tools")

The factory:

  1. Reads the current request ID from the contextvar (set by the
     inbound ``KilnRequestIDMiddleware``).
  2. Adds it as the ``X-Kiln-Request-ID`` request header before send.
  3. Honors any caller-supplied header — if you explicitly pass
     ``headers={"X-Kiln-Request-ID": "..."}`` that wins.
  4. Falls back to a fresh UUID4 hex if there's no active request
     context (background tasks, scripts, tests). This guarantees the
     downstream service still gets a non-empty correlation ID.

Both async and sync flavors are exported because some Kiln code paths
(loaders, fixtures) use the sync API.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from kiln_shared.request_id import REQUEST_ID_HEADER, current_request_id


def _resolved_request_id() -> str:
    """Return the current request ID, or a fresh UUID4 if none is set.

    Background tasks and unit tests don't run inside an HTTP request, so
    ``current_request_id()`` returns the placeholder ``"-"``. We mint a
    real ID in that case so downstream services don't see junk.
    """
    rid = current_request_id()
    if rid and rid != "-":
        return rid
    return uuid.uuid4().hex


async def _attach_request_id_async(request: httpx.Request) -> None:
    """httpx requires async event hooks on AsyncClient — sync hooks crash."""
    if REQUEST_ID_HEADER not in request.headers:
        request.headers[REQUEST_ID_HEADER] = _resolved_request_id()


def _attach_request_id_sync(request: httpx.Request) -> None:
    """Sync hook for the sync client."""
    if REQUEST_ID_HEADER not in request.headers:
        request.headers[REQUEST_ID_HEADER] = _resolved_request_id()


def async_client(**kwargs: Any) -> httpx.AsyncClient:
    """Return an ``httpx.AsyncClient`` with request-ID forwarding wired in.

    All ``kwargs`` are passed through to the underlying client. The
    ``event_hooks`` argument is merged so callers can still register
    their own hooks alongside ours.
    """
    user_hooks = kwargs.pop("event_hooks", {}) or {}
    request_hooks = list(user_hooks.get("request", []))
    request_hooks.append(_attach_request_id_async)
    merged: dict[str, list] = {
        "request": request_hooks,
        "response": list(user_hooks.get("response", [])),
    }
    return httpx.AsyncClient(event_hooks=merged, **kwargs)


def sync_client(**kwargs: Any) -> httpx.Client:
    """Sync counterpart of :func:`async_client`."""
    user_hooks = kwargs.pop("event_hooks", {}) or {}
    request_hooks = list(user_hooks.get("request", []))
    request_hooks.append(_attach_request_id_sync)
    merged: dict[str, list] = {
        "request": request_hooks,
        "response": list(user_hooks.get("response", [])),
    }
    return httpx.Client(event_hooks=merged, **kwargs)
