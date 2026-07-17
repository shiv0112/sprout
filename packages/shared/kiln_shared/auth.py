"""
kiln_shared.auth
----------------
FastAPI authentication dependencies for Kiln services.

Supports two auth methods:
  1. Clerk JWT (Authorization: Bearer <token>) — for browser clients
  2. API key (X-API-Key: kiln_<user_id>_<hex>) — for CLI/MCP clients

Usage in FastAPI routes:
    from kiln_shared.auth import require_auth, require_jwt_auth, KilnUser

    @app.post("/tools/register")
    async def register_tool(user: KilnUser = Depends(require_auth)):
        print(f"Authenticated as {user.email}")

    @app.post("/auth/api-key")
    async def create_api_key(user: KilnUser = Depends(require_jwt_auth)):
        # JWT only — no API key fallback
        ...
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request

from .config import get_config

logger = logging.getLogger(__name__)

# ── JWKS Cache ────────────────────────────────────────────────────────────────

_jwks_cache: dict[str, Any] | None = None
_jwks_cache_time: float = 0
_JWKS_CACHE_TTL = 3600  # 1 hour

# ── API Key Cache ─────────────────────────────────────────────────────────────

_api_key_cache: dict[str, tuple[KilnUser, float]] = {}
_API_KEY_CACHE_TTL = 300  # 5 minutes


@dataclass(frozen=True)
class KilnUser:
    """Authenticated user info extracted from JWT or API key."""

    user_id: str
    email: str
    name: str


# ── JWKS Fetching ─────────────────────────────────────────────────────────────


async def _get_jwks(clerk_domain: str) -> dict[str, Any]:
    """Fetch and cache Clerk's JWKS for JWT verification."""
    global _jwks_cache, _jwks_cache_time  # noqa: PLW0603

    now = time.time()
    if _jwks_cache is not None and (now - _jwks_cache_time) < _JWKS_CACHE_TTL:
        return _jwks_cache

    url = f"https://{clerk_domain}/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_cache_time = now
        return _jwks_cache


# ── JWT Verification ──────────────────────────────────────────────────────────


async def _verify_jwt(token: str) -> KilnUser:
    """Verify a Clerk JWT and extract user info."""
    config = get_config()
    if not config.clerk_domain:
        raise HTTPException(status_code=500, detail="CLERK_DOMAIN not configured")

    try:
        jwks_data = await _get_jwks(config.clerk_domain)
        jwk_set = jwt.PyJWKSet.from_dict(jwks_data)

        # Match the signing key from JWKS to the token's kid header
        token_header = jwt.get_unverified_header(token)
        kid = token_header.get("kid")
        signing_key = None
        for key in jwk_set.keys:
            if key.key_id == kid:
                signing_key = key
                break
        if signing_key is None:
            raise HTTPException(status_code=401, detail="No matching signing key found")

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=f"https://{config.clerk_domain}",
            options={"verify_aud": False},
        )

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token: missing 'sub' claim")

        return KilnUser(
            user_id=user_id,
            email=payload.get("email") or payload.get("email_address") or "",
            name=payload.get("name") or payload.get("first_name") or "",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired") from None
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}") from None
    except Exception as e:
        logger.error("JWT verification failed: %s", e)
        raise HTTPException(status_code=401, detail="Authentication failed") from None


# ── API Key Verification ──────────────────────────────────────────────────────


async def _verify_api_key(api_key: str) -> KilnUser:
    """
    Verify an API key by extracting the user_id from the key format
    (kiln_<user_id>_<hex>) and confirming against Clerk user metadata.
    """
    config = get_config()
    if not config.clerk_secret_key:
        raise HTTPException(status_code=500, detail="CLERK_SECRET_KEY not configured")

    # Check cache first
    now = time.time()
    if api_key in _api_key_cache:
        user, cached_at = _api_key_cache[api_key]
        if (now - cached_at) < _API_KEY_CACHE_TTL:
            return user
        del _api_key_cache[api_key]

    # Parse key format: kiln_<user_id>_<32-char-hex>
    # user_id may contain underscores (e.g., user_2abc123), so we extract
    # the last 32 chars as hex and everything between "kiln_" and the hex as user_id
    if not api_key.startswith("kiln_") or len(api_key) < 38:  # "kiln_" + at least 1 char + "_" + 32 hex
        raise HTTPException(status_code=401, detail="Invalid API key format") from None

    # Last 32 chars are the hex token, preceded by an underscore
    hex_part = api_key[-32:]
    middle = api_key[5:-32]  # everything between "kiln_" and the hex
    if not middle.endswith("_") or not all(c in "0123456789abcdef" for c in hex_part):
        raise HTTPException(status_code=401, detail="Invalid API key format") from None

    user_id = middle[:-1]  # strip trailing underscore

    # Fetch user from Clerk Backend API
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.clerk.com/v1/users/{user_id}",
                headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
            )
            if resp.status_code == 404:
                raise HTTPException(status_code=401, detail="Invalid API key") from None
            resp.raise_for_status()
            user_data = resp.json()
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=401, detail="Invalid API key") from None
    except httpx.RequestError as e:
        logger.error("Clerk API request failed: %s", e)
        raise HTTPException(status_code=503, detail="Auth service unavailable") from None

    # Verify key matches stored metadata
    stored_key = user_data.get("private_metadata", {}).get("api_key", "")
    if stored_key != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key") from None

    user = KilnUser(
        user_id=user_id,
        email=user_data.get("email_addresses", [{}])[0].get("email_address", "")
        if user_data.get("email_addresses")
        else "",
        name=f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip(),
    )

    # Cache successful lookup
    _api_key_cache[api_key] = (user, now)
    return user


def invalidate_api_key_cache(api_key: str) -> None:
    """Remove a specific API key from the verification cache."""
    _api_key_cache.pop(api_key, None)


# ── Internal Auth ─────────────────────────────────────────────────────────────


def verify_internal_secret(request: Request) -> None:
    """Verify X-Internal-Secret header for service-to-service calls."""
    config = get_config()
    if not config.internal_secret:
        return  # No secret configured = skip validation (local dev)

    secret = request.headers.get("X-Internal-Secret", "")
    if secret != config.internal_secret:
        raise HTTPException(status_code=403, detail="Invalid internal secret")


# ── FastAPI Dependencies ──────────────────────────────────────────────────────


async def require_auth(request: Request) -> KilnUser:
    """
    FastAPI dependency: authenticate via JWT, API key, or internal secret.
    Raises 401 if none are valid.

    Side effect: stores the resolved ``KilnUser`` on ``request.state.user``
    so downstream middleware (e.g. ``kiln_shared.rate_limit.kiln_user_key``)
    can read it without re-running the auth chain.
    """
    user: KilnUser | None = None

    # Try JWT first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user = await _verify_jwt(token)
    else:
        # Fall back to API key
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            user = await _verify_api_key(api_key)
        else:
            # Fall back to internal service secret (for service-to-service calls)
            internal_secret = request.headers.get("X-Internal-Secret", "")
            if internal_secret:
                config = get_config()
                if config.internal_secret and internal_secret == config.internal_secret:
                    user = KilnUser(user_id="internal", email="", name="internal-service")

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide Authorization: Bearer <jwt> or X-API-Key: <key>",
        )
    request.state.user = user
    return user


async def require_jwt_auth(request: Request) -> KilnUser:
    """
    FastAPI dependency: JWT only, no API key fallback.
    Used for auth management endpoints (API key generation/rotation).
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="JWT authentication required. Provide Authorization: Bearer <jwt>",
        )

    token = auth_header[7:]
    return await _verify_jwt(token)
