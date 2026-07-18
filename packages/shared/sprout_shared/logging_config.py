"""
sprout_shared.logging_config
--------------------------
Structured logging configuration for all Sprout services.

Two output formats:

- ``text`` (default for local dev): human-readable single-line records
  including the request ID for correlation across services
- ``json``  (recommended for production / Cloud Run / GKE): one JSON
  object per line, ready for ingestion by Loki, Datadog, Cloud Logging

Switch with the ``SPROUT_LOG_FORMAT`` environment variable.

The configuration always installs a ``RequestIDLoggingFilter`` so any
log record emitted inside an HTTP request automatically picks up the
``X-Sprout-Request-ID`` set by the ``SproutRequestIDMiddleware``.

Usage in service main.py:

    from sprout_shared.logging_config import setup_logging
    from sprout_shared.request_id import SproutRequestIDMiddleware

    app = FastAPI()
    app.add_middleware(SproutRequestIDMiddleware)
    setup_logging()
"""

from __future__ import annotations

import json
import logging
import os
import sys

from sprout_shared.request_id import RequestIDLoggingFilter


class _JsonFormatter(logging.Formatter):
    """One-line JSON formatter for production ingestion.

    Includes the standard fields plus ``request_id`` (set by the request
    ID filter) and ``exception`` for tracebacks. Extra attributes set
    via ``logger.info(..., extra={"foo": "bar"})`` are preserved at the
    top level so structured queries can filter on them.
    """

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "asctime", "request_id", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Surface ``extra=`` attributes as top-level keys.
        for key, value in record.__dict__.items():
            if key in self._RESERVED:
                continue
            if key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for Sprout services."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    log_format = os.environ.get("SPROUT_LOG_FORMAT", "text").lower()

    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-7s | %(name)s | rid=%(request_id)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    # Inject request ID onto every record so the format placeholder works
    # even outside an HTTP request (defaults to "-").
    handler.addFilter(RequestIDLoggingFilter())

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
