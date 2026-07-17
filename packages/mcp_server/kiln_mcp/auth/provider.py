from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from kiln_mcp.auth.store import InMemoryOAuthStore

logger = logging.getLogger(__name__)

_ACCESS_TOKEN_TTL = 3600
_REFRESH_TOKEN_TTL = 2592000

_ephemeral_secret: bytes | None = None


def _signing_secret() -> bytes:
    configured = os.environ.get("KILN_INTERNAL_SECRET", "").strip()
    if configured:
        return configured.encode()
    global _ephemeral_secret  # noqa: PLW0603
    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_bytes(32)
    return _ephemeral_secret


def _sign_state(payload: bytes) -> str:
    mac = hmac.new(_signing_secret(), payload, hashlib.sha256).digest()
    return urlsafe_b64encode(mac).decode().rstrip("=")


def verify_state(encoded: str) -> dict | None:
    try:
        payload_b64, sig_b64 = encoded.split(".", 1)
        payload_bytes = urlsafe_b64decode(payload_b64 + "==")
        expected_sig = _sign_state(payload_bytes)
        if not hmac.compare_digest(expected_sig, sig_b64):
            return None
        return json.loads(payload_bytes)
    except (ValueError, json.JSONDecodeError):
        return None


class KilnAuthorizationCode(AuthorizationCode):
    user_id: str


class KilnAccessToken(AccessToken):
    user_id: str
    paired_refresh_token: str | None = None


class KilnRefreshToken(RefreshToken):
    user_id: str
    paired_access_token: str | None = None


class KilnOAuthProvider:
    def __init__(
        self,
        store: InMemoryOAuthStore,
        clerk_domain: str,
        issuer_url: str,
    ) -> None:
        self._store = store
        self._clerk_domain = clerk_domain
        self._issuer_url = issuer_url.rstrip("/")

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self._store.get_client(client_id)
        if data is None:
            return None
        return OAuthClientInformationFull(**data)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._store.save_client(client_info.client_id, client_info.model_dump(mode="json"))

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        registered = {str(u) for u in client.redirect_uris}
        if str(params.redirect_uri) not in registered:
            raise ValueError(
                f"redirect_uri {params.redirect_uri} is not registered for client {client.client_id}"
            )

        state_payload = {
            "oauth_state": params.state,
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "client_id": client.client_id,
            "scopes": params.scopes or [],
        }
        payload_bytes = json.dumps(state_payload).encode()
        payload_b64 = urlsafe_b64encode(payload_bytes).decode().rstrip("=")
        signature = _sign_state(payload_bytes)
        encoded_state = f"{payload_b64}.{signature}"

        callback_url = f"{self._issuer_url}/oauth/callback"
        ui_base = os.environ.get("KILN_UI_URL", "").rstrip("/")
        if ui_base:
            return (
                f"{ui_base}/mcp-auth?"
                + urlencode({"callback": f"{callback_url}?state={encoded_state}"})
            )
        return (
            f"https://{self._clerk_domain}/sign-in?"
            + urlencode({"redirect_url": f"{callback_url}?state={encoded_state}"})
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> KilnAuthorizationCode | None:
        data = self._store.get_auth_code(authorization_code)
        if data is None:
            return None
        if data.get("client_id") != client.client_id:
            return None
        return KilnAuthorizationCode(**data)

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: KilnAuthorizationCode,
    ) -> OAuthToken:
        self._store.delete_auth_code(authorization_code.code)

        now = int(time.time())
        access_token_str = secrets.token_urlsafe(32)
        refresh_token_str = secrets.token_urlsafe(32)

        access_token = KilnAccessToken(
            token=access_token_str,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=now + _ACCESS_TOKEN_TTL,
            user_id=authorization_code.user_id,
            paired_refresh_token=refresh_token_str,
        )
        refresh_token = KilnRefreshToken(
            token=refresh_token_str,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            user_id=authorization_code.user_id,
            paired_access_token=access_token_str,
        )

        self._store.save_access_token(
            access_token_str, access_token.model_dump(), ttl=_ACCESS_TOKEN_TTL
        )
        self._store.save_refresh_token(
            refresh_token_str, refresh_token.model_dump(), ttl=_REFRESH_TOKEN_TTL
        )

        return OAuthToken(
            access_token=access_token_str,
            refresh_token=refresh_token_str,
            token_type="Bearer",
            expires_in=_ACCESS_TOKEN_TTL,
        )

    async def load_access_token(self, token: str) -> KilnAccessToken | None:
        data = self._store.get_access_token(token)
        if data is None:
            return None
        return KilnAccessToken(**data)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> KilnRefreshToken | None:
        data = self._store.get_refresh_token(refresh_token)
        if data is None:
            return None
        if data.get("client_id") != client.client_id:
            return None
        return KilnRefreshToken(**data)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: KilnRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self._store.delete_refresh_token(refresh_token.token)
        if refresh_token.paired_access_token:
            self._store.delete_access_token(refresh_token.paired_access_token)

        now = int(time.time())
        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)

        access_token = KilnAccessToken(
            token=new_access,
            client_id=client.client_id,
            scopes=scopes or refresh_token.scopes,
            expires_at=now + _ACCESS_TOKEN_TTL,
            user_id=refresh_token.user_id,
            paired_refresh_token=new_refresh,
        )
        new_refresh_token = KilnRefreshToken(
            token=new_refresh,
            client_id=client.client_id,
            scopes=scopes or refresh_token.scopes,
            user_id=refresh_token.user_id,
            paired_access_token=new_access,
        )

        self._store.save_access_token(
            new_access, access_token.model_dump(), ttl=_ACCESS_TOKEN_TTL
        )
        self._store.save_refresh_token(
            new_refresh, new_refresh_token.model_dump(), ttl=_REFRESH_TOKEN_TTL
        )

        return OAuthToken(
            access_token=new_access,
            refresh_token=new_refresh,
            token_type="Bearer",
            expires_in=_ACCESS_TOKEN_TTL,
        )

    async def revoke_token(self, token: KilnAccessToken | KilnRefreshToken) -> None:
        if isinstance(token, KilnAccessToken):
            self._store.delete_access_token(token.token)
            if token.paired_refresh_token:
                self._store.delete_refresh_token(token.paired_refresh_token)
        elif isinstance(token, KilnRefreshToken):
            self._store.delete_refresh_token(token.token)
            if token.paired_access_token:
                self._store.delete_access_token(token.paired_access_token)
        else:
            logger.warning("revoke_token called with unknown type: %s", type(token).__name__)
