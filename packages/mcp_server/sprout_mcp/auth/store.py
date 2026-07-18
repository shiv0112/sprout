from __future__ import annotations

import time
from typing import Any


class InMemoryOAuthStore:
    """TTL-aware in-memory store for OAuth clients, codes, and tokens."""

    def __init__(self) -> None:
        self._clients: dict[str, dict[str, Any]] = {}
        self._auth_codes: dict[str, tuple[dict[str, Any], float]] = {}
        self._access_tokens: dict[str, tuple[dict[str, Any], float]] = {}
        self._refresh_tokens: dict[str, tuple[dict[str, Any], float]] = {}

    def save_client(self, client_id: str, data: dict[str, Any]) -> None:
        self._clients[client_id] = data

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        return self._clients.get(client_id)

    def delete_client(self, client_id: str) -> None:
        self._clients.pop(client_id, None)

    def save_auth_code(self, code: str, data: dict[str, Any], *, ttl: int) -> None:
        self._auth_codes[code] = (data, time.time() + ttl)

    def get_auth_code(self, code: str) -> dict[str, Any] | None:
        entry = self._auth_codes.get(code)
        if entry is None:
            return None
        data, expires_at = entry
        if time.time() > expires_at:
            del self._auth_codes[code]
            return None
        return data

    def delete_auth_code(self, code: str) -> None:
        self._auth_codes.pop(code, None)

    def save_access_token(self, token: str, data: dict[str, Any], *, ttl: int) -> None:
        self._access_tokens[token] = (data, time.time() + ttl)

    def get_access_token(self, token: str) -> dict[str, Any] | None:
        entry = self._access_tokens.get(token)
        if entry is None:
            return None
        data, expires_at = entry
        if time.time() > expires_at:
            del self._access_tokens[token]
            return None
        return data

    def delete_access_token(self, token: str) -> None:
        self._access_tokens.pop(token, None)

    def save_refresh_token(self, token: str, data: dict[str, Any], *, ttl: int) -> None:
        self._refresh_tokens[token] = (data, time.time() + ttl)

    def get_refresh_token(self, token: str) -> dict[str, Any] | None:
        entry = self._refresh_tokens.get(token)
        if entry is None:
            return None
        data, expires_at = entry
        if time.time() > expires_at:
            del self._refresh_tokens[token]
            return None
        return data

    def delete_refresh_token(self, token: str) -> None:
        self._refresh_tokens.pop(token, None)

    def cleanup(self) -> None:
        now = time.time()
        for store in (self._auth_codes, self._access_tokens, self._refresh_tokens):
            expired = [k for k, (_, exp) in list(store.items()) if now > exp]
            for k in expired:
                store.pop(k, None)
