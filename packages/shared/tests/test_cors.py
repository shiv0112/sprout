"""Tests for the shared CORS installer.

Pin the contract:
  - Dev environment defaults to the localhost allowlist
  - Explicit CORS_ORIGINS env var always wins
  - SPROUT_ENV=production WITHOUT CORS_ORIGINS raises at install time
  - The installed middleware exposes X-Sprout-Request-ID on responses
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sprout_shared.cors import install_cors, resolve_cors_origins


def test_resolve_origins_defaults_to_localhost_in_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("SPROUT_ENV", raising=False)
    origins = resolve_cors_origins()
    assert "http://localhost:3001" in origins


def test_resolve_origins_honors_explicit_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://sprout.example,https://other.example")
    assert resolve_cors_origins() == [
        "https://sprout.example",
        "https://other.example",
    ]


def test_resolve_origins_raises_in_production_without_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production without CORS_ORIGINS must fail loudly — never open up
    the dev localhost allowlist to an accidentally-public deploy."""
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("SPROUT_ENV", "production")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS must be set"):
        resolve_cors_origins()


def test_resolve_origins_prod_with_explicit_config_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPROUT_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://sprout.example")
    assert resolve_cors_origins() == ["https://sprout.example"]


def test_install_cors_exposes_request_id_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Browser JS must be able to read X-Sprout-Request-ID off every response.

    The default expose list must include it — otherwise the frontend
    can't echo the ID to support channels when the user files a bug.
    """
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("SPROUT_ENV", raising=False)

    app = FastAPI()
    install_cors(app)

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    client = TestClient(app)
    # Use a real GET with Origin header (not OPTIONS) — the
    # access-control-expose-headers field only appears on actual CORS
    # responses, not preflights.
    resp = client.get("/ping", headers={"Origin": "http://localhost:3001"})
    assert resp.status_code == 200
    # Starlette normalises header keys to lowercase.
    expose = resp.headers.get("access-control-expose-headers", "").lower()
    assert "x-sprout-request-id" in expose
