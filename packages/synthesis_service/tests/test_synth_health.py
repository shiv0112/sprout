"""Tests for synthesis_service /livez, /readyz, and /health endpoints.

Module name is test_synth_health (not test_health) to avoid mypy module
collisions with chat_backend/tests/test_health.py and registry_api/
tests/test_main.py — without __init__.py files mypy still tries to
resolve test files by basename and complains about duplicates.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from sprout_synthesis.main import app

    return TestClient(app)


class _FakeAsyncClient:
    """Same shape as the chat_backend / mcp_server test fakes."""

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
    import sprout_synthesis.routes.health as health_mod

    def _fake_async_client(**_kw: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(responses)

    monkeypatch.setattr(health_mod, "async_client", _fake_async_client)


def _registry_url() -> str:
    """Compute the URL the readyz handler will hit, given the current settings."""
    from sprout_synthesis.routes.health import _registry_livez_url

    return _registry_livez_url()


def test_livez_is_cheap_and_returns_200(client: TestClient) -> None:
    resp = client.get("/livez")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "sprout-synthesis-service"
    assert "checks" not in body


def test_readyz_returns_ok_when_registry_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx(monkeypatch, {_registry_url(): 200})
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "sprout-synthesis-service"
    assert body["checks"]["registry_callback"].startswith("ok")


def test_readyz_returns_503_when_registry_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx(monkeypatch, {})
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert "unreachable" in body["checks"]["registry_callback"]


def test_health_legacy_compat_keeps_version_field(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy /health route still includes the version field for old callers."""
    _patch_httpx(monkeypatch, {_registry_url(): 200})
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"
    assert body["service"] == "sprout-synthesis-service"
