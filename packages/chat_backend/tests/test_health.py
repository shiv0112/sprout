"""Tests for chat_backend's /livez, /readyz, and /health endpoints.

Stubs the httpx client so we don't need a real registry_api running.
Pins the iter-37 contract: liveness is cheap, readiness pings the
registry_api as a hard dependency, synthesis_service as a soft one.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    """A bare TestClient — startup is a no-op for chat_backend."""
    from kiln_chat_backend.main import app

    return TestClient(app)


def test_livez_is_cheap_and_returns_200(client: TestClient) -> None:
    """Liveness must NOT call out to dependencies."""
    resp = client.get("/livez")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "kiln-chat-backend"
    assert "checks" not in body, "/livez must stay cheap — no dependency checks"


class _FakeAsyncClient:
    """Minimal async-context-manager mock of httpx.AsyncClient.

    Returns a stubbed response based on a per-URL map. Unmapped URLs raise
    ConnectionError to simulate an unreachable downstream.
    """

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
    """Replace async_client inside chat_backend.main with our fake."""
    import kiln_chat_backend.main as main_mod

    def _fake_async_client(**_kw: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(responses)

    monkeypatch.setattr(main_mod, "async_client", _fake_async_client)


def test_readyz_returns_ok_when_registry_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx(
        monkeypatch,
        {
            "http://localhost:8766/livez": 200,
            "http://localhost:8002/health": 200,
        },
    )
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "kiln-chat-backend"
    assert body["checks"]["registry_api"] == "ok"
    assert body["checks"]["synthesis_service"] == "ok"
    assert "active_runs" in body


def test_readyz_returns_503_when_registry_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registry is a HARD dependency — its absence must produce a 503.

    Synthesis is also unreachable here but it's soft and shouldn't cause
    the 503 on its own; the 503 is driven by the registry failure.
    """
    _patch_httpx(monkeypatch, {})  # everything unreachable
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert "unreachable" in body["checks"]["registry_api"]


def test_readyz_returns_ok_when_only_synthesis_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthesis is a SOFT dependency. Its absence must NOT fail readyz.

    Pin this contract: a registry-up + synthesis-down stack should still
    be able to serve runs that use only existing tools.
    """
    _patch_httpx(
        monkeypatch,
        {"http://localhost:8766/livez": 200},  # synthesis omitted
    )
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["registry_api"] == "ok"
    assert "unreachable" in body["checks"]["synthesis_service"]
    assert "optional" in body["checks"]["synthesis_service"]


def test_health_legacy_compat_includes_active_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy /health route still includes ``active_runs`` for old callers."""
    _patch_httpx(
        monkeypatch,
        {
            "http://localhost:8766/livez": 200,
            "http://localhost:8002/health": 200,
        },
    )
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "active_runs" in body
