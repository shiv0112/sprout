"""
kiln_shared.cors
----------------
CORS middleware installer shared across all Kiln FastAPI services.

Design goals:

1. **One source of truth** — all services call ``install_cors(app)``
   instead of each re-writing the same ``add_middleware(CORSMiddleware, ...)``
   block with slightly different typos.
2. **Strict method + header allowlist** — no more ``allow_methods=["*"]``
   / ``allow_headers=["*"]``. Explicit lists catch future footguns
   (e.g. a new custom header nobody remembered to add to the allowlist
   will fail loudly in dev).
3. **Production enforcement** — if ``KILN_ENV=production`` and
   ``CORS_ORIGINS`` isn't set, the installer raises at startup rather
   than silently opening up the dev localhost allowlist. This fail-loud
   behaviour is the cheapest way to catch a misconfigured prod deploy.
4. **Dev-friendly default** — when ``KILN_ENV`` is unset or "development",
   the installer uses ``http://localhost:3001`` (the registry_ui) plus
   the two legacy Vite ports.

Usage in service main.py::

    from kiln_shared.cors import install_cors

    app = FastAPI()
    install_cors(app)
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


# Methods every Kiln service needs. Kept explicit so adding a new one
# (PATCH? TRACE?) is a deliberate choice, not a wildcard inheritance.
_DEFAULT_METHODS: list[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]

# Headers clients are allowed to send. Matches what the UI and MCP clients
# actually use today — anything else is a footgun.
_DEFAULT_REQUEST_HEADERS: list[str] = [
    "Authorization",
    "Content-Type",
    "X-API-Key",
    "X-Internal-Secret",
    "X-Kiln-Request-ID",
]

# Headers the browser is allowed to READ from our responses (CORS
# default hides custom headers). Exposing X-Kiln-Request-ID lets the
# frontend echo the ID back to support channels when filing a bug.
_DEFAULT_EXPOSE_HEADERS: list[str] = [
    "X-Kiln-Request-ID",
    "X-RateLimit-Limit",
    "Retry-After",
]

_DEV_DEFAULT_ORIGINS: list[str] = [
    "http://localhost:3001",
    "http://localhost:5173",
    "http://localhost:5174",
]


def _parse_origins(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


def resolve_cors_origins() -> list[str]:
    """Return the active CORS allowlist, honoring env vars + KILN_ENV.

    Rules:
    - ``CORS_ORIGINS`` (comma-separated) is always honored when set.
    - Otherwise, dev environments default to localhost:3001/5173/5174.
    - Production environments WITHOUT ``CORS_ORIGINS`` raise RuntimeError
      so the service won't silently start with a permissive default.
    """
    explicit = _parse_origins(os.environ.get("CORS_ORIGINS"))
    if explicit:
        return explicit

    env = os.environ.get("KILN_ENV", "development").lower()
    if env in {"production", "prod"}:
        raise RuntimeError(
            "CORS_ORIGINS must be set when KILN_ENV=production. "
            "Refusing to start with the dev localhost allowlist in production. "
            "Set CORS_ORIGINS='https://your.domain,https://other.domain' in the "
            "service's environment configuration."
        )
    return _DEV_DEFAULT_ORIGINS


def install_cors(
    app: FastAPI,
    *,
    extra_expose_headers: list[str] | None = None,
) -> None:
    """Add the Kiln CORS middleware to a FastAPI app with strict defaults.

    ``extra_expose_headers`` lets a service add its own response headers
    to the browser-readable list (on top of the defaults).
    """
    origins = resolve_cors_origins()
    expose = list(_DEFAULT_EXPOSE_HEADERS)
    if extra_expose_headers:
        for h in extra_expose_headers:
            if h not in expose:
                expose.append(h)

    logger.info(
        "Installing CORS with allowlist=%s methods=%s expose=%s",
        origins,
        _DEFAULT_METHODS,
        expose,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=_DEFAULT_METHODS,
        allow_headers=_DEFAULT_REQUEST_HEADERS,
        allow_credentials=True,
        expose_headers=expose,
    )
