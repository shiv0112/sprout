"""
kiln_shared.request_id
----------------------
Per-request correlation IDs for distributed tracing.

Adds an ``X-Kiln-Request-ID`` header to every request/response and binds
the ID into a contextvar so that any log record emitted while processing
the request automatically inherits it. This is the bare minimum for
correlating logs across services without a full OpenTelemetry stack.

Usage in service main.py:

    from fastapi import FastAPI
    from kiln_shared.request_id import KilnRequestIDMiddleware

    app = FastAPI()
    app.add_middleware(KilnRequestIDMiddleware)

After mounting the middleware, calling ``setup_logging()`` from
``kiln_shared.logging_config`` automatically picks up the contextvar
via the registered logging filter.

Inside route handlers you can read the current ID via:

    from kiln_shared.request_id import current_request_id
    rid = current_request_id()
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Header name we accept on inbound requests AND emit on outbound responses.
# Matches the header used by curl/clients across the Kiln stack.
REQUEST_ID_HEADER = "X-Kiln-Request-ID"

# Default value when there's no active request (background tasks, scripts).
_DEFAULT = "-"

_request_id_ctx: ContextVar[str] = ContextVar("kiln_request_id", default=_DEFAULT)


def current_request_id() -> str:
    """Return the current request's correlation ID, or ``"-"`` if none."""
    return _request_id_ctx.get()


def set_request_id(request_id: str) -> None:
    """Force-set the request ID — for tests, background workers, CLI scripts."""
    _request_id_ctx.set(request_id)


class RequestIDLoggingFilter(logging.Filter):
    """Logging filter that injects the current request ID onto every record.

    The standard library's ``Formatter`` reads attributes off the LogRecord
    via ``%(name)s``-style placeholders. We add ``record.request_id`` here
    so formatters can include ``%(request_id)s`` in their format string.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


class KilnRequestIDMiddleware(BaseHTTPMiddleware):
    """ASGI middleware: assign + propagate ``X-Kiln-Request-ID``.

    - Reads the inbound header if the caller already supplied one (so a
      chain of services can share an ID).
    - Generates a fresh UUID4 hex if absent.
    - Stores on ``request.state.request_id`` for handler access.
    - Sets the contextvar so all log lines emitted during the request
      inherit the ID via ``RequestIDLoggingFilter``.
    - Echoes the ID back on the response header so clients can correlate.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = _request_id_ctx.set(rid)
        request.state.request_id = rid
        try:
            response: Response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response
