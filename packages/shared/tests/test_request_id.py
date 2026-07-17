"""Tests for KilnRequestIDMiddleware + the logging filter integration.

Pin the production-readiness contract:
  - Inbound X-Kiln-Request-ID is honored (chain of services share an ID)
  - Missing inbound header → fresh UUID assigned
  - Response always echoes the header
  - Contextvar carries the ID into log records via the filter
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kiln_shared.request_id import (
    REQUEST_ID_HEADER,
    KilnRequestIDMiddleware,
    RequestIDLoggingFilter,
    current_request_id,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(KilnRequestIDMiddleware)

    @app.get("/echo")
    def echo() -> dict:
        return {"request_id": current_request_id()}

    return app


def test_response_echoes_inbound_request_id() -> None:
    """A client-supplied X-Kiln-Request-ID must be propagated unchanged."""
    client = TestClient(_build_app())
    resp = client.get("/echo", headers={REQUEST_ID_HEADER: "test-fixture-123"})
    assert resp.status_code == 200
    assert resp.headers[REQUEST_ID_HEADER] == "test-fixture-123"
    assert resp.json()["request_id"] == "test-fixture-123"


def test_response_assigns_fresh_request_id_when_missing() -> None:
    """No inbound header → middleware mints a fresh UUID per request."""
    client = TestClient(_build_app())
    resp = client.get("/echo")
    assert resp.status_code == 200
    rid = resp.headers[REQUEST_ID_HEADER]
    assert len(rid) == 32  # UUID4 hex
    assert rid == resp.json()["request_id"]


def test_each_request_gets_a_unique_id() -> None:
    client = TestClient(_build_app())
    rids = {client.get("/echo").headers[REQUEST_ID_HEADER] for _ in range(5)}
    assert len(rids) == 5


def test_logging_filter_injects_request_id_attribute() -> None:
    """``record.request_id`` is set so formatters can use ``%(request_id)s``."""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    flt = RequestIDLoggingFilter()
    assert flt.filter(record) is True
    assert hasattr(record, "request_id")
    # Outside an HTTP request the contextvar default is "-"
    assert record.request_id == "-"


def test_current_request_id_default_outside_request() -> None:
    """Background tasks / scripts get the placeholder default."""
    assert current_request_id() == "-"
