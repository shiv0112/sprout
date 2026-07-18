"""
sprout_mcp/main.py
----------------
Sprout MCP Server -- bridges the MCP JSON-RPC protocol to the Sprout Registry API
with OAuth 2.1 + PKCE authentication (delegating user identity to Clerk).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

import mcp.server.auth.routes as _mcp_auth_routes
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from sprout_mcp import creation
from sprout_mcp import tools as tool_module
from sprout_mcp.auth.clerk_callback import build_callback_route
from sprout_mcp.auth.provider import SproutOAuthProvider
from sprout_mcp.auth.store import InMemoryOAuthStore
from sprout_mcp.user_env import fetch_user_env_vars
from sprout_shared.env import required_url as _required_url
from sprout_shared.httpx_client import async_client
from sprout_shared.request_id import SproutRequestIDMiddleware

logger = logging.getLogger(__name__)

_original_validate = _mcp_auth_routes.validate_issuer_url


def _patched_validate_issuer_url(issuer_url) -> None:
    try:
        _original_validate(issuer_url)
    except ValueError:
        if os.environ.get("SPROUT_MCP_ALLOW_HTTP_ISSUER", "").lower() in {"true", "1"}:
            logger.warning("Allowing HTTP issuer URL: %s", issuer_url)
            return
        raise


_mcp_auth_routes.validate_issuer_url = _patched_validate_issuer_url

REGISTRY_URL = _required_url("SPROUT_REGISTRY_URL", "http://localhost:8766")
POLL_INTERVAL = int(os.environ.get("SPROUT_MCP_POLL_INTERVAL", "30"))


def _resolve_issuer_url() -> str:
    """Public OAuth issuer URL for the MCP server.

    An explicit ``SPROUT_MCP_ISSUER_URL`` always wins. Otherwise, when running
    on Render, derive it from the platform-injected ``RENDER_EXTERNAL_URL``
    plus the ``/mcp`` path prefix that the Caddy reverse proxy routes on
    (see docker/render/Caddyfile) — so the issuer needs no manual per-deploy
    config. Falls back to the localhost dev default (or fails fast in prod
    when neither is set), exactly as before.
    """
    explicit = os.environ.get("SPROUT_MCP_ISSUER_URL")
    if explicit:
        return explicit
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        return f"{render_url.rstrip('/')}/mcp"
    return _required_url("SPROUT_MCP_ISSUER_URL", "http://localhost:8768")


ISSUER_URL = _resolve_issuer_url()
CLERK_DOMAIN = os.environ.get("CLERK_DOMAIN", "").strip()

_oauth_store = InMemoryOAuthStore()
_oauth_provider = SproutOAuthProvider(
    store=_oauth_store,
    clerk_domain=CLERK_DOMAIN,
    issuer_url=ISSUER_URL,
)

_auth_enabled = bool(CLERK_DOMAIN)


def _build_mcp() -> FastMCP:
    from urllib.parse import urlparse
    issuer_host = urlparse(ISSUER_URL).hostname or "localhost"
    transport = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[issuer_host, f"{issuer_host}:*"],
        allowed_origins=[ISSUER_URL, f"https://{issuer_host}", f"http://{issuer_host}"],
    )
    common_kwargs = dict(
        instructions=(
            "Sprout is a self-evolving tool registry. Before creating anything "
            "new, call `sprout_route_intent` (best match) or `sprout_search_tools` "
            "(ranked list) to see if an existing tool already covers the task. "
            "If no tool matches, use `sprout_create_tool` to publish one — you "
            "write the Python yourself and this server registers, validates, "
            "and sandboxes it."
        ),
        stateless_http=True,
        json_response=True,
        transport_security=transport,
    )
    if _auth_enabled:
        return FastMCP(
            "Sprout",
            **common_kwargs,
            auth_server_provider=_oauth_provider,
            auth=AuthSettings(
                issuer_url=AnyHttpUrl(ISSUER_URL),
                resource_server_url=None,
                client_registration_options=ClientRegistrationOptions(
                    enabled=True,
                    valid_scopes=["sprout:tools"],
                    default_scopes=["sprout:tools"],
                ),
            ),
        )
    logger.warning(
        "CLERK_DOMAIN not set -- OAuth disabled, running unauthenticated (dev only)"
    )
    return FastMCP("Sprout", **common_kwargs)


mcp = _build_mcp()


def _current_user_id() -> str | None:
    token = get_access_token()
    if token is None:
        return None
    return getattr(token, "user_id", None)


@mcp.tool()
async def sprout_search_tools(
    query: str,
    ctx: Context[ServerSession, None],
    limit: int = 8,
) -> str:
    """Find tools in the Sprout registry that match a natural-language query.

    Ranks the whole registry by semantic similarity (BM25 over tool names,
    descriptions, tags, and ids) so paraphrases work — "ycombinator news"
    finds hackernews_top, "latest bitcoin price" finds crypto_price. Prefer
    this over sprout_route_intent when the caller wants to browse several
    candidates; use sprout_route_intent when a single best match is needed.
    """
    await ctx.info(f"Semantic search: {query}")
    limit = max(1, min(limit, 25))
    try:
        async with async_client(timeout=10) as client:
            resp = await client.get(
                f"{REGISTRY_URL}/tools/search",
                params={"q": query, "mode": "semantic", "limit": limit},
            )
            resp.raise_for_status()
            results = resp.json()
    except Exception as exc:
        return json.dumps({"error": f"Search failed: {exc}"})

    if not results:
        return json.dumps({"query": query, "matches": []})

    matches = [
        {
            "id": t["id"],
            "name": t["name"],
            "description": t.get("description", ""),
            "confidence": t.get("confidence"),
            "score": t.get("score"),
        }
        for t in results
    ]
    return json.dumps({"query": query, "matches": matches}, indent=2)


@mcp.tool()
async def sprout_route_intent(
    intent: str,
    ctx: Context[ServerSession, None],
    min_confidence: float = 0.0,
) -> str:
    """Route a natural-language intent to the single best Sprout tool.

    Use before creating a new tool: ask "which tool solves X?" first, and
    synthesize only if the top match's confidence is too low. `min_confidence`
    (0..1) is a hard gate — set it to 0.82 or higher to suppress weak matches
    and fall through to tool creation.
    """
    await ctx.info(f"Routing intent: {intent}")
    try:
        async with async_client(timeout=10) as client:
            resp = await client.post(
                f"{REGISTRY_URL}/tools/route",
                json={"intent": intent, "min_confidence": min_confidence, "limit": 3},
            )
            resp.raise_for_status()
            return json.dumps(resp.json(), indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Routing failed: {exc}"})


@mcp.tool()
async def sprout_refresh_tools(ctx: Context[ServerSession, None]) -> str:
    """Pull the latest tool catalog from the Sprout registry.

    Idempotent. New tools are registered; tools removed upstream are
    unregistered locally so stale handlers don't leak into the client's
    tool list.
    """
    await ctx.info("Refreshing tools from registry...")
    added, removed = await tool_module.sync_tools(mcp)
    if added or removed:
        with contextlib.suppress(Exception):
            await ctx.session.send_tool_list_changed()
    total = tool_module.get_registered_tool_count()
    return json.dumps({"added": added, "removed": removed, "total": total})


@mcp.tool()
async def sprout_registry_stats(ctx: Context[ServerSession, None]) -> str:
    """Return registry-wide statistics (total tools, categories, tags, authors)."""
    await ctx.info("Fetching registry stats")
    try:
        async with async_client(timeout=10) as client:
            resp = await client.get(f"{REGISTRY_URL}/tools/stats")
            resp.raise_for_status()
            return json.dumps(resp.json(), indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Failed to fetch stats: {exc}"})


@mcp.tool()
async def sprout_create_tool(
    tool_id: str,
    name: str,
    description: str,
    params: list[dict[str, Any]],
    impl_code: str,
    ctx: Context[ServerSession, None],
    returns: dict[str, Any] | None = None,
    dependencies: list[str] | None = None,
    tags: list[str] | None = None,
    category: str = "general",
    version: str = "1.0.0",
    author: str = "mcp_client",
    required_env_vars: list[str] | None = None,
) -> str:
    """Publish a new tool to the Sprout registry from code you (the MCP client) write.

    Sprout deliberately does not invoke an LLM here — you generate the spec
    and the Python implementation yourself, and this call registers it.
    The server validates the spec schema, runs any declared test fixtures,
    rejects blocked imports, and makes the tool callable from every agent
    framework immediately on success.

    Arguments:
        tool_id:      dotted identifier, e.g. "com.sprout.tools.my_weather".
        name:         Python identifier; becomes the function name and the
                      entrypoint filename.
        description:  one-sentence explanation shown to the LLM to help it
                      decide when to call this tool.
        params:       list of {name, type, description, required, default?, enum?}.
                      `type` is one of str/int/float/bool/list/dict/any.
        impl_code:    full Python source. Must define a function matching
                      `name` (sync or async). Blocked imports are rejected
                      server-side.
        returns:      optional {type, description} for the output.
        dependencies: pip specs, e.g. ["requests>=2.28"]. Installed in a
                      sandbox at execution time; keep the set small.
        tags:         free-form labels for catalog grouping.
        category:     broad bucket like "data", "media", "productivity".
        required_env_vars: UPPER_SNAKE env var names the impl reads
                      (e.g. ["OPENAI_API_KEY"]). Must match what impl_code
                      references. Each entry must be on the Sprout provider
                      allowlist. The sandbox will inject ONLY these names
                      (from the invoking user's saved keys) at execution
                      time — everything else is absent from os.environ.
    """
    await ctx.info(f"Creating tool {tool_id}")
    declared = list(required_env_vars or [])
    user_id = _current_user_id()
    try:
        # Parse once and share the AST across every validator — avoids a
        # second ast.parse() per create call.
        tree = creation.parse_impl(impl_code)
        detected = creation.detect_env_var_refs(tree)
        creation.reconcile_env_vars(detected=detected, declared=declared)
        spec_yaml = creation.build_spec_yaml(
            tool_id=tool_id,
            name=name,
            description=description,
            params=params,
            returns=returns,
            dependencies=dependencies,
            version=version,
            author=author,
            tags=tags,
            category=category,
            required_env_vars=declared,
        )
        creation.validate_impl_defines_function(tree, name)
        result = await creation.submit_to_registry(
            spec_yaml=spec_yaml,
            impl_code=impl_code,
            entrypoint=f"{name}.py",
            user_id=user_id,
        )
    except creation.ToolCreationError as exc:
        return json.dumps({"success": False, "error": str(exc)})
    except Exception as exc:
        logger.exception("sprout_create_tool failed")
        return json.dumps({"success": False, "error": f"unexpected: {exc}"})

    added, removed = await tool_module.sync_tools(mcp)
    with contextlib.suppress(Exception):
        await ctx.session.send_tool_list_changed()

    result["mcp_catalog"] = {"added": added, "removed": removed}
    result["required_env_vars"] = await _env_var_status(declared, user_id=user_id)
    result["unused_declarations"] = sorted(set(declared) - detected.literals)
    result["setup_hint"] = (
        "Add any 'already_set: false' keys at Sprout → Settings → Tool Env Vars. "
        "Each invoking user supplies their own keys; the creator's keys are not "
        "shared."
    )
    return json.dumps(result, indent=2)


async def _env_var_status(
    declared: list[str], *, user_id: str | None
) -> list[dict[str, object]]:
    """Return `[{name, already_set}]` for each declared var.

    When unauthenticated (dev/stdio), `already_set` is None so the AI client
    can still surface the names without pretending to know the user's state.
    """
    if not declared:
        return []
    if user_id is None:
        return [{"name": n, "already_set": None} for n in declared]
    saved = await fetch_user_env_vars(user_id)
    return [{"name": n, "already_set": n in saved} for n in declared]


async def _livez(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "sprout-mcp-server"})


async def _readyz(_request: Request) -> JSONResponse:
    checks: dict[str, str] = {}
    overall = "ok"

    try:
        async with async_client(timeout=2.0) as client:
            resp = await client.get(f"{REGISTRY_URL}/livez")
            if resp.status_code == 200:
                checks["registry_api"] = "ok"
            else:
                checks["registry_api"] = f"unhealthy: HTTP {resp.status_code}"
                overall = "degraded"
    except Exception as exc:
        checks["registry_api"] = f"unreachable: {exc!s}"
        overall = "degraded"

    checks["registered_tools"] = f"ok ({tool_module.get_registered_tool_count()} tools)"
    checks["auth"] = (
        "enabled (clerk)" if _auth_enabled else "disabled (CLERK_DOMAIN not set)"
    )

    body = {"status": overall, "service": "sprout-mcp-server", "checks": checks}
    if overall != "ok":
        return JSONResponse(status_code=503, content=body)
    return JSONResponse(body)


async def _health(request: Request) -> JSONResponse:
    return await _readyz(request)


async def _poll_registry() -> None:
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            added, removed = await asyncio.wait_for(
                tool_module.sync_tools(mcp),
                timeout=POLL_INTERVAL * 0.8,
            )
            if added or removed:
                logger.info("Registry poll: +%d / -%d tools", added, removed)
        except TimeoutError:
            logger.error("Registry poll timed out")
        except Exception as e:
            logger.error("Registry poll failed: %s", e)


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            _oauth_store.cleanup()
        except Exception as e:
            logger.error("OAuth store cleanup failed: %s", e)


def build_http_app() -> Starlette:
    mcp_app = mcp.streamable_http_app()

    extra_routes: list[Route] = [
        Route("/livez", _livez, methods=["GET"]),
        Route("/readyz", _readyz, methods=["GET"]),
        Route("/health", _health, methods=["GET"]),
    ]

    if _auth_enabled:
        extra_routes.append(
            build_callback_route(store=_oauth_store, clerk_domain=CLERK_DOMAIN)
        )

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with mcp_app.router.lifespan_context(_app):
            try:
                await tool_module.sync_tools(mcp)
                logger.info(
                    "Initial tool sync complete (%d tools)",
                    tool_module.get_registered_tool_count(),
                )
            except Exception as e:
                logger.error("Initial tool sync failed: %s", e)

            poll_task = asyncio.create_task(_poll_registry())
            cleanup_task = (
                asyncio.create_task(_cleanup_loop()) if _auth_enabled else None
            )
            try:
                yield
            finally:
                poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poll_task
                if cleanup_task is not None:
                    cleanup_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await cleanup_task

    return Starlette(
        routes=[*extra_routes, Mount("/", app=mcp_app)],
        middleware=[Middleware(SproutRequestIDMiddleware)],
        lifespan=lifespan,
    )


def main():
    import sys

    from sprout_shared.logging_config import setup_logging
    setup_logging()

    transport = sys.argv[1] if len(sys.argv) > 1 else "streamable-http"
    host = os.environ.get("SPROUT_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("SPROUT_MCP_PORT", "8768"))

    logger.info(
        "Starting Sprout MCP Server on %s:%s (transport: %s, auth: %s)",
        host, port, transport, "enabled" if _auth_enabled else "disabled",
    )

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn
        app = build_http_app()
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
