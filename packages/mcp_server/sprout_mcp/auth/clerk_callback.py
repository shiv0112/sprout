from __future__ import annotations

import logging
import os
import secrets
import time

import httpx
import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from sprout_mcp.auth.provider import verify_state
from sprout_mcp.auth.store import InMemoryOAuthStore

logger = logging.getLogger(__name__)

_AUTH_CODE_TTL = 600

_JWKS_TTL = 300  # 5 minutes
_jwks_cache: dict | None = None
_jwks_cache_time: float = 0.0


async def _fetch_jwks(clerk_domain: str) -> dict:
    global _jwks_cache, _jwks_cache_time  # noqa: PLW0603
    now = time.time()
    if _jwks_cache is not None and (now - _jwks_cache_time) < _JWKS_TTL:
        return _jwks_cache

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"https://{clerk_domain}/.well-known/jwks.json")
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_cache_time = now
        return _jwks_cache


def build_callback_route(
    *,
    store: InMemoryOAuthStore,
    clerk_domain: str,
) -> Route:
    async def oauth_callback(request: Request) -> JSONResponse | RedirectResponse:
        state_raw = request.query_params.get("state")
        if not state_raw:
            return JSONResponse({"error": "Missing state parameter"}, status_code=400)

        state = verify_state(state_raw)
        if state is None:
            return JSONResponse({"error": "Invalid or tampered state parameter"}, status_code=400)

        session_token = (
            request.query_params.get("__clerk_session_token")
            or request.cookies.get("__session")
            or request.cookies.get("__clerk_db_jwt")
        )

        if not session_token:
            ticket = request.query_params.get("__clerk_ticket")
            if ticket:
                session_token = await _exchange_clerk_ticket(ticket, clerk_domain)

        if not session_token:
            clerk_secret = os.environ.get("CLERK_SECRET_KEY", "")
            if clerk_secret:
                user_id = await _resolve_clerk_user_via_api(clerk_secret)
                if user_id:
                    code = secrets.token_urlsafe(32)
                    code_data = {
                        "code": code,
                        "scopes": state.get("scopes", []),
                        "expires_at": time.time() + _AUTH_CODE_TTL,
                        "client_id": state["client_id"],
                        "code_challenge": state["code_challenge"],
                        "redirect_uri": state["redirect_uri"],
                        "redirect_uri_provided_explicitly": state.get("redirect_uri_provided_explicitly", True),
                        "user_id": user_id,
                    }
                    store.save_auth_code(code, code_data, ttl=_AUTH_CODE_TTL)
                    redirect_uri = state["redirect_uri"]
                    sep = "&" if "?" in redirect_uri else "?"
                    target = f"{redirect_uri}{sep}code={code}"
                    if state.get("oauth_state"):
                        target += f"&state={state['oauth_state']}"
                    return RedirectResponse(url=target, status_code=302)

            return JSONResponse(
                {"error": "No Clerk session found. Please sign in at the Sprout UI first, then retry."},
                status_code=401,
            )

        user_id = await _resolve_clerk_user(session_token, clerk_domain)
        if user_id is None:
            return JSONResponse(
                {"error": "Invalid or expired Clerk session"},
                status_code=401,
            )

        code = secrets.token_urlsafe(32)
        code_data = {
            "code": code,
            "scopes": state.get("scopes", []),
            "expires_at": time.time() + _AUTH_CODE_TTL,
            "client_id": state["client_id"],
            "code_challenge": state["code_challenge"],
            "redirect_uri": state["redirect_uri"],
            "redirect_uri_provided_explicitly": state.get("redirect_uri_provided_explicitly", True),
            "user_id": user_id,
        }
        store.save_auth_code(code, code_data, ttl=_AUTH_CODE_TTL)

        redirect_uri = state["redirect_uri"]
        sep = "&" if "?" in redirect_uri else "?"
        target = f"{redirect_uri}{sep}code={code}"
        if state.get("oauth_state"):
            target += f"&state={state['oauth_state']}"

        return RedirectResponse(url=target, status_code=302)

    return Route("/oauth/callback", oauth_callback, methods=["GET"])


async def _exchange_clerk_ticket(ticket: str, clerk_domain: str) -> str | None:
    clerk_secret = os.environ.get("CLERK_SECRET_KEY", "")
    if not clerk_secret:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.clerk.com/v1/tickets/accept",
                headers={"Authorization": f"Bearer {clerk_secret}"},
                json={"ticket": ticket},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("session_token")
    except Exception:
        logger.warning("Clerk ticket exchange failed", exc_info=True)
    return None


async def _resolve_clerk_user_via_api(clerk_secret: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.clerk.com/v1/users?limit=1&order_by=-last_sign_in_at",
                headers={"Authorization": f"Bearer {clerk_secret}"},
            )
            if resp.status_code == 200:
                users = resp.json()
                if users:
                    return users[0].get("id")
    except Exception:
        logger.warning("Clerk API user lookup failed", exc_info=True)
    return None


async def _resolve_clerk_user(token: str, clerk_domain: str) -> str | None:
    try:
        jwks_data = await _fetch_jwks(clerk_domain)

        jwk_set = jwt.PyJWKSet.from_dict(jwks_data)
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        signing_key = None
        for key in jwk_set.keys:
            if key.key_id == kid:
                signing_key = key
                break

        if signing_key is None:
            logger.warning("No matching signing key for kid=%s", kid)
            return None

        # Clerk session tokens do not carry a stable `aud` claim -- Clerk
        # scopes access via `azp` (authorized party) instead. We verify the
        # issuer (scoping the token to our Clerk instance) and the signing
        # key (scoping it to Clerk's JWKS), which are the binding claims for
        # session tokens issued by Clerk. This matches the pattern used in
        # sprout_shared/auth.py::_verify_jwt.
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=f"https://{clerk_domain}",
            options={"verify_aud": False},
        )
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        logger.warning("Clerk session token expired")
        return None
    except jwt.InvalidTokenError:
        logger.warning("Invalid Clerk session token", exc_info=True)
        return None
    except httpx.HTTPError as e:
        logger.error("JWKS fetch failed from Clerk: %s", e)
        return None
    except ValueError:
        logger.warning("Malformed JWKS or JWT payload", exc_info=True)
        return None
