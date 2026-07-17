from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kiln_mcp.user_env import _cache, fetch_user_env_vars


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    _cache.clear()


@pytest.mark.asyncio
async def test_returns_env_vars_from_clerk() -> None:
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {
        "private_metadata": {"tool_env_vars": {"NEWS_API_KEY": "abc123"}}
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with (
        patch("kiln_mcp.user_env.httpx.AsyncClient", return_value=mock_client),
        patch.dict("os.environ", {"CLERK_SECRET_KEY": "sk_test_123"}),
    ):
        result = await fetch_user_env_vars("user_123")

    assert result == {"NEWS_API_KEY": "abc123"}


@pytest.mark.asyncio
async def test_returns_empty_dict_when_no_clerk_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        result = await fetch_user_env_vars("user_123")
    assert result == {}


@pytest.mark.asyncio
async def test_returns_empty_dict_on_clerk_error() -> None:
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    import httpx as _httpx_err
    mock_client.get = AsyncMock(side_effect=_httpx_err.ConnectError("connection refused"))

    with (
        patch("kiln_mcp.user_env.httpx.AsyncClient", return_value=mock_client),
        patch.dict("os.environ", {"CLERK_SECRET_KEY": "sk_test_123"}),
    ):
        result = await fetch_user_env_vars("user_123")

    assert result == {}


@pytest.mark.asyncio
async def test_caches_result() -> None:
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {
        "private_metadata": {"tool_env_vars": {"KEY": "val"}}
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with (
        patch("kiln_mcp.user_env.httpx.AsyncClient", return_value=mock_client),
        patch.dict("os.environ", {"CLERK_SECRET_KEY": "sk_test_123"}),
    ):
        r1 = await fetch_user_env_vars("user_123")
        r2 = await fetch_user_env_vars("user_123")

    assert r1 == r2 == {"KEY": "val"}
    assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_cache_expires_after_ttl() -> None:
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {
        "private_metadata": {"tool_env_vars": {"KEY": "val"}}
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with (
        patch("kiln_mcp.user_env.httpx.AsyncClient", return_value=mock_client),
        patch.dict("os.environ", {"CLERK_SECRET_KEY": "sk_test_123"}),
    ):
        with patch("kiln_mcp.user_env.time.time", return_value=1000.0):
            await fetch_user_env_vars("user_123")
        with patch("kiln_mcp.user_env.time.time", return_value=1500.0):
            await fetch_user_env_vars("user_123")

    assert mock_client.get.call_count == 2
