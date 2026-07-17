"""Tests for mcp_server's /livez, /readyz, and /health endpoints.

The mcp_server uses FastMCP wrapped in a parent Starlette app. We test
the readyz handler directly without spinning up the full MCP transport
(which needs a session manager and an event loop) by calling it as a
plain async function with a stub Request.
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeAsyncClient:
    """Same shape as chat_backend's test_health._FakeAsyncClient."""

    def __init__(self, responses: dict[str, int]) -> None:
        self._responses = responses

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def get(self, url: str) -> Any:
        if url in self._responses:
            class _Resp:
                status_code = self._responses[url]
            return _Resp()
        raise ConnectionError(f"fake: {url} unreachable")


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, responses: dict[str, int]) -> None:
    import kiln_mcp.main as main_mod

    def _fake_async_client(**_kw: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(responses)

    monkeypatch.setattr(main_mod, "async_client", _fake_async_client)


@pytest.mark.asyncio
async def test_livez_is_cheap_and_returns_200() -> None:
    from kiln_mcp.main import _livez

    resp = await _livez(None)  # type: ignore[arg-type]
    assert resp.status_code == 200
    import json

    body = json.loads(resp.body)
    assert body["status"] == "ok"
    assert body["service"] == "kiln-mcp-server"
    assert "checks" not in body


@pytest.mark.asyncio
async def test_readyz_returns_ok_when_registry_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx(monkeypatch, {"http://localhost:8766/livez": 200})
    from kiln_mcp.main import _readyz

    resp = await _readyz(None)  # type: ignore[arg-type]
    assert resp.status_code == 200
    import json

    body = json.loads(resp.body)
    assert body["status"] == "ok"
    assert body["service"] == "kiln-mcp-server"
    assert body["checks"]["registry_api"] == "ok"
    assert body["checks"]["registered_tools"].startswith("ok")


@pytest.mark.asyncio
async def test_readyz_returns_503_when_registry_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx(monkeypatch, {})  # everything unreachable
    from kiln_mcp.main import _readyz

    resp = await _readyz(None)  # type: ignore[arg-type]
    assert resp.status_code == 503
    import json

    body = json.loads(resp.body)
    assert body["status"] == "degraded"
    assert "unreachable" in body["checks"]["registry_api"]


def test_build_http_app_exposes_health_routes() -> None:
    """The wrapper app must include /livez /readyz /health routes alongside MCP."""
    from kiln_mcp.main import build_http_app

    app = build_http_app()
    paths = {
        getattr(route, "path", None) for route in app.routes
    }
    assert "/livez" in paths
    assert "/readyz" in paths
    assert "/health" in paths
