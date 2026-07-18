from __future__ import annotations

import json
import time

import pytest
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from sprout_mcp.auth.provider import (
    SproutAccessToken,
    SproutAuthorizationCode,
    SproutOAuthProvider,
)
from sprout_mcp.auth.store import InMemoryOAuthStore


@pytest.fixture
def store() -> InMemoryOAuthStore:
    return InMemoryOAuthStore()


@pytest.fixture
def provider(store: InMemoryOAuthStore) -> SproutOAuthProvider:
    return SproutOAuthProvider(
        store=store,
        clerk_domain="test.clerk.accounts.dev",
        issuer_url="http://localhost:8768",
    )


def _make_client_info(client_id: str = "test-client") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_name="Test Client",
        redirect_uris=[AnyUrl("http://localhost:3000/callback")],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


@pytest.mark.asyncio
async def test_register_and_get_client(provider: SproutOAuthProvider) -> None:
    info = _make_client_info()
    await provider.register_client(info)
    result = await provider.get_client("test-client")
    assert result is not None
    assert result.client_id == "test-client"


@pytest.mark.asyncio
async def test_get_unknown_client_returns_none(provider: SproutOAuthProvider) -> None:
    result = await provider.get_client("unknown")
    assert result is None


@pytest.mark.asyncio
async def test_authorize_returns_clerk_redirect(provider: SproutOAuthProvider) -> None:
    from mcp.server.auth.provider import AuthorizationParams

    info = _make_client_info()
    await provider.register_client(info)
    params = AuthorizationParams(
        state="xyz",
        scopes=["sprout:tools"],
        code_challenge="challenge123",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(info, params)
    assert "test.clerk.accounts.dev" in url
    assert "oauth%2Fcallback" in url or "oauth/callback" in url


@pytest.mark.asyncio
async def test_exchange_authorization_code(provider: SproutOAuthProvider) -> None:
    info = _make_client_info()
    await provider.register_client(info)

    auth_code = SproutAuthorizationCode(
        code="test-code",
        scopes=["sprout:tools"],
        expires_at=time.time() + 600,
        client_id="test-client",
        code_challenge="challenge",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
        user_id="user_abc",
    )
    provider._store.save_auth_code("test-code", auth_code.model_dump(mode="json"), ttl=600)

    token = await provider.exchange_authorization_code(info, auth_code)
    assert token.access_token
    assert token.refresh_token
    assert token.expires_in == 3600


@pytest.mark.asyncio
async def test_load_access_token(provider: SproutOAuthProvider) -> None:
    info = _make_client_info()
    await provider.register_client(info)

    auth_code = SproutAuthorizationCode(
        code="code2",
        scopes=["sprout:tools"],
        expires_at=time.time() + 600,
        client_id="test-client",
        code_challenge="ch",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
        user_id="user_xyz",
    )
    provider._store.save_auth_code("code2", auth_code.model_dump(mode="json"), ttl=600)
    token_resp = await provider.exchange_authorization_code(info, auth_code)

    loaded = await provider.load_access_token(token_resp.access_token)
    assert loaded is not None
    assert loaded.user_id == "user_xyz"
    assert loaded.client_id == "test-client"


@pytest.mark.asyncio
async def test_load_expired_access_token_returns_none(provider: SproutOAuthProvider) -> None:
    provider._store.save_access_token(
        "expired",
        SproutAccessToken(
            token="expired",
            client_id="c",
            scopes=["sprout:tools"],
            expires_at=int(time.time()) - 10,
            user_id="u",
        ).model_dump(),
        ttl=-1,
    )
    loaded = await provider.load_access_token("expired")
    assert loaded is None


@pytest.mark.asyncio
async def test_exchange_refresh_token(provider: SproutOAuthProvider) -> None:
    info = _make_client_info()
    await provider.register_client(info)

    auth_code = SproutAuthorizationCode(
        code="code3",
        scopes=["sprout:tools"],
        expires_at=time.time() + 600,
        client_id="test-client",
        code_challenge="ch",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
        user_id="user_refresh",
    )
    provider._store.save_auth_code("code3", auth_code.model_dump(mode="json"), ttl=600)
    token_resp = await provider.exchange_authorization_code(info, auth_code)

    refresh = await provider.load_refresh_token(info, token_resp.refresh_token)
    assert refresh is not None

    new_token = await provider.exchange_refresh_token(info, refresh, ["sprout:tools"])
    assert new_token.access_token != token_resp.access_token
    assert new_token.refresh_token != token_resp.refresh_token


@pytest.mark.asyncio
async def test_revoke_token(provider: SproutOAuthProvider) -> None:
    at = SproutAccessToken(
        token="revoke-me",
        client_id="c",
        scopes=["sprout:tools"],
        expires_at=int(time.time()) + 3600,
        user_id="u",
    )
    provider._store.save_access_token("revoke-me", at.model_dump(), ttl=3600)

    await provider.revoke_token(at)
    assert await provider.load_access_token("revoke-me") is None


@pytest.mark.asyncio
async def test_auth_code_deleted_after_exchange(provider: SproutOAuthProvider) -> None:
    info = _make_client_info()
    await provider.register_client(info)

    auth_code = SproutAuthorizationCode(
        code="single-use",
        scopes=["sprout:tools"],
        expires_at=time.time() + 600,
        client_id="test-client",
        code_challenge="ch",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
        user_id="user",
    )
    provider._store.save_auth_code("single-use", auth_code.model_dump(mode="json"), ttl=600)

    await provider.exchange_authorization_code(info, auth_code)

    assert provider._store.get_auth_code("single-use") is None


@pytest.mark.asyncio
async def test_refresh_token_invalidated_after_exchange(provider: SproutOAuthProvider) -> None:
    info = _make_client_info()
    await provider.register_client(info)

    auth_code = SproutAuthorizationCode(
        code="code-inv",
        scopes=["sprout:tools"],
        expires_at=time.time() + 600,
        client_id="test-client",
        code_challenge="ch",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
        user_id="user",
    )
    provider._store.save_auth_code("code-inv", auth_code.model_dump(mode="json"), ttl=600)
    token_resp = await provider.exchange_authorization_code(info, auth_code)

    refresh = await provider.load_refresh_token(info, token_resp.refresh_token)
    assert refresh is not None

    await provider.exchange_refresh_token(info, refresh, ["sprout:tools"])

    assert await provider.load_refresh_token(info, token_resp.refresh_token) is None


@pytest.mark.asyncio
async def test_revoke_refresh_token(provider: SproutOAuthProvider) -> None:
    info = _make_client_info()
    await provider.register_client(info)

    auth_code = SproutAuthorizationCode(
        code="code-rev",
        scopes=["sprout:tools"],
        expires_at=time.time() + 600,
        client_id="test-client",
        code_challenge="ch",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
        user_id="user",
    )
    provider._store.save_auth_code("code-rev", auth_code.model_dump(mode="json"), ttl=600)
    token_resp = await provider.exchange_authorization_code(info, auth_code)

    refresh = await provider.load_refresh_token(info, token_resp.refresh_token)
    assert refresh is not None

    await provider.revoke_token(refresh)

    assert await provider.load_refresh_token(info, token_resp.refresh_token) is None
    assert await provider.load_access_token(token_resp.access_token) is None


@pytest.mark.asyncio
async def test_revoke_access_also_revokes_paired_refresh(provider: SproutOAuthProvider) -> None:
    info = _make_client_info()
    await provider.register_client(info)

    auth_code = SproutAuthorizationCode(
        code="code-pair",
        scopes=["sprout:tools"],
        expires_at=time.time() + 600,
        client_id="test-client",
        code_challenge="ch",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
        user_id="user",
    )
    provider._store.save_auth_code("code-pair", auth_code.model_dump(mode="json"), ttl=600)
    token_resp = await provider.exchange_authorization_code(info, auth_code)

    access = await provider.load_access_token(token_resp.access_token)
    assert access is not None

    await provider.revoke_token(access)

    assert await provider.load_access_token(token_resp.access_token) is None
    assert await provider.load_refresh_token(info, token_resp.refresh_token) is None


def test_verify_state_accepts_signed_payload() -> None:
    from base64 import urlsafe_b64encode

    from sprout_mcp.auth.provider import _sign_state, verify_state

    payload = json.dumps({"hello": "world"}).encode()
    sig = _sign_state(payload)
    encoded = f"{urlsafe_b64encode(payload).decode().rstrip('=')}.{sig}"

    result = verify_state(encoded)
    assert result == {"hello": "world"}


def test_verify_state_rejects_tampered_payload() -> None:
    from base64 import urlsafe_b64encode

    from sprout_mcp.auth.provider import _sign_state, verify_state

    payload = json.dumps({"client_id": "victim"}).encode()
    sig = _sign_state(payload)
    tampered = json.dumps({"client_id": "attacker"}).encode()
    encoded = f"{urlsafe_b64encode(tampered).decode().rstrip('=')}.{sig}"

    assert verify_state(encoded) is None


@pytest.mark.asyncio
async def test_authorize_rejects_unregistered_redirect_uri(provider: SproutOAuthProvider) -> None:
    from mcp.server.auth.provider import AuthorizationParams

    info = _make_client_info()
    await provider.register_client(info)

    params = AuthorizationParams(
        state="x",
        scopes=["sprout:tools"],
        code_challenge="ch",
        redirect_uri=AnyUrl("http://evil.example.com/callback"),
        redirect_uri_provided_explicitly=True,
    )
    with pytest.raises(ValueError, match="not registered"):
        await provider.authorize(info, params)


def test_verify_state_rejects_malformed_input() -> None:
    from sprout_mcp.auth.provider import verify_state

    assert verify_state("not-valid-at-all") is None
    assert verify_state("no.dot.separator.count") is None


@pytest.mark.asyncio
async def test_exchange_refresh_invalidates_old_access_token(provider: SproutOAuthProvider) -> None:
    """Old access token must be revoked when refresh token is rotated."""
    info = _make_client_info()
    await provider.register_client(info)

    auth_code = SproutAuthorizationCode(
        code="code-rot",
        scopes=["sprout:tools"],
        expires_at=time.time() + 600,
        client_id="test-client",
        code_challenge="ch",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
        user_id="user",
    )
    provider._store.save_auth_code("code-rot", auth_code.model_dump(mode="json"), ttl=600)
    token_resp = await provider.exchange_authorization_code(info, auth_code)

    refresh = await provider.load_refresh_token(info, token_resp.refresh_token)
    assert refresh is not None

    await provider.exchange_refresh_token(info, refresh, ["sprout:tools"])

    assert await provider.load_access_token(token_resp.access_token) is None
