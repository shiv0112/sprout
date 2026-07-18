"""
sprout_mcp/tools.py
-----------------
Tool registration, handler creation, and execution for the Sprout MCP server.

Fetches tool specs from the Sprout Registry API, generates typed async handlers
via exec(), and registers them with FastMCP. Handles polling-driven refresh
with name-collision detection and stale-tool tracking.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

import httpx
from mcp.server.auth.middleware.auth_context import get_access_token

from sprout_mcp.user_env import fetch_user_env_vars
from sprout_shared.env import required_url
from sprout_shared.httpx_client import async_client

logger = logging.getLogger(__name__)

REGISTRY_URL = required_url("SPROUT_REGISTRY_URL", "http://localhost:8766")

_registered_tools: dict[str, dict] = {}
_registered_names: dict[str, str] = {}
_stale_tool_ids: set[str] = set()
_tool_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _tool_lock
    if _tool_lock is None:
        _tool_lock = asyncio.Lock()
    return _tool_lock


def _build_mcp_tool_schema(tool_spec: dict) -> dict:
    type_map = {
        "str": "string", "int": "integer", "float": "number",
        "bool": "boolean", "list": "array", "dict": "object",
    }
    properties: dict[str, dict] = {}
    required: list[str] = []
    for p in tool_spec.get("params", []):
        prop: dict = {
            "type": type_map.get(p.get("type", "str"), "string"),
            "description": p.get("description", ""),
        }
        if p.get("enum"):
            prop["enum"] = p["enum"]
        if p.get("default") is not None:
            prop["default"] = p["default"]
        properties[p["name"]] = prop
        if p.get("required", True):
            required.append(p["name"])

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


async def _execute_tool(tool_id: str, args: dict, *, user_id: str | None = None) -> dict:
    env_vars: dict[str, str] = {}
    if user_id:
        saved = await fetch_user_env_vars(user_id)
        # Only forward the env vars this tool explicitly declared — everything
        # else stays on the MCP server and never touches the registry's wire.
        # The registry re-enforces the same intersection; this is the outer
        # layer of defense in depth.
        declared = _declared_env_vars(tool_id)
        env_vars = {k: saved[k] for k in declared if k in saved and saved[k]}

    body: dict[str, Any] = {"args": args}
    if env_vars:
        body["env_vars"] = env_vars

    headers = {"X-Internal-Secret": os.environ.get("SPROUT_INTERNAL_SECRET", "")}
    if user_id:
        headers["X-Sprout-User-ID"] = user_id

    async with async_client(timeout=30) as client:
        resp = await client.post(
            f"{REGISTRY_URL}/tools/{tool_id}/execute",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


def _declared_env_vars(tool_id: str) -> frozenset[str]:
    """Read declared env vars from the tool spec cached in `_registered_tools`.

    The cache is populated by `sync_tools` (GET /tools). Returns an empty set
    for tools registered before this field existed, which matches the fallback
    behavior in the registry's spec loader.
    """
    spec = _registered_tools.get(tool_id)
    if spec is None:
        return frozenset()
    return frozenset(spec.get("required_env_vars") or ())


async def _execute_tool_safe(tool_id: str, args: dict) -> str:
    """Execute a tool and return a JSON string; never raises.

    Extracts the authenticated user_id from the current request context
    (set by FastMCP's AuthContextMiddleware). If no auth is configured
    or no token is present, user_id is None and env var injection is
    skipped.
    """
    user_id: str | None = None
    token = get_access_token()
    if token is not None:
        user_id = getattr(token, "user_id", None)

    if tool_id in _stale_tool_ids:
        return json.dumps({
            "error": f"Tool {tool_id} has been removed from the registry. Call sprout_refresh_tools.",
        })

    try:
        result = await _execute_tool(tool_id, args, user_id=user_id)
        if result.get("success"):
            return json.dumps(result.get("result", {}), indent=2, default=str)
        return json.dumps({"error": result.get("detail", "Unknown error")})
    except httpx.TimeoutException:
        logger.error("Tool execution timed out: %s", tool_id)
        return json.dumps({"error": "Tool execution timed out after 30s"})
    except httpx.HTTPStatusError as e:
        error_detail = e.response.text
        with contextlib.suppress(Exception):
            error_detail = e.response.json().get("detail", error_detail)
        if e.response.status_code < 500:
            return json.dumps({"error": str(error_detail)})
        logger.error("Tool execution server error for %s: %s", tool_id, error_detail)
        return json.dumps({"error": "Tool execution failed on the server"})
    except Exception as e:
        logger.exception("Unexpected error executing tool %s", tool_id)
        return json.dumps({"error": str(e)})


def _make_tool_handler(tool_id: str, tool_spec: dict):
    name = tool_spec["name"]
    description = tool_spec.get("description", "").replace('"""', "'''")
    params = tool_spec.get("params", [])

    if not name.isidentifier():
        raise ValueError(f"Invalid tool name (not a Python identifier): {name!r}")
    for p in params:
        pname = p.get("name", "")
        if not pname.isidentifier():
            raise ValueError(f"Invalid param name for tool {name}: {pname!r}")

    _TYPE_MAP = {
        "str": "str", "int": "int", "float": "float",
        "bool": "bool", "list": "list", "dict": "dict",
    }

    sig_parts = []
    for p in params:
        t = _TYPE_MAP.get(p.get("type", "str"), "str")
        if not p.get("required", True) and p.get("default") is not None:
            sig_parts.append(f"{p['name']}: {t} = {repr(p['default'])}")
        elif not p.get("required", True):
            sig_parts.append(f"{p['name']}: {t} = None")
        else:
            sig_parts.append(f"{p['name']}: {t}")

    sig_str = ", ".join(sig_parts)
    kwargs_str = ", ".join(f'"{p["name"]}": {p["name"]}' for p in params)

    tool_id_repr = repr(tool_id)
    fn_source = f'''
async def {name}({sig_str}) -> str:
    """{description}"""
    _args = {{{kwargs_str}}}
    return await _safe_exec({tool_id_repr}, _args)
'''

    namespace = {"_safe_exec": _execute_tool_safe}
    exec(fn_source, namespace)  # noqa: S102
    return namespace[name]


async def _fetch_tools() -> list[dict]:
    try:
        async with async_client(timeout=10) as client:
            resp = await client.get(f"{REGISTRY_URL}/tools")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error("Failed to fetch tools from registry: %s", e)
        return []


async def sync_tools(mcp_server) -> tuple[int, int]:
    tools = await _fetch_tools()
    if not tools:
        return 0, 0

    async with _get_lock():
        current_ids = set(_registered_tools.keys())
        new_ids = {t["id"] for t in tools}

        added = 0
        for tool in tools:
            tid = tool["id"]
            tname = tool["name"]
            _stale_tool_ids.discard(tid)

            if tid in current_ids:
                continue

            existing_id = _registered_names.get(tname)
            if existing_id is not None and existing_id != tid:
                logger.error(
                    "Tool name collision: '%s' is already registered as %s; skipping %s",
                    tname, existing_id, tid,
                )
                continue

            try:
                handler = _make_tool_handler(tid, tool)
            except ValueError as e:
                logger.error("Skipping tool %s: %s", tid, e)
                continue
            mcp_server.tool(name=tname, description=tool.get("description", ""))(handler)
            _registered_tools[tid] = tool
            _registered_names[tname] = tid
            added += 1
            logger.info("Registered MCP tool: %s (%s)", tname, tid)

        removed = 0
        for rid in current_ids - new_ids:
            stale_spec = _registered_tools.pop(rid, None)
            if stale_spec is None:
                continue
            stale_name = stale_spec.get("name", "")
            _registered_names.pop(stale_name, None)
            _stale_tool_ids.add(rid)
            if stale_name:
                with contextlib.suppress(Exception):
                    mcp_server.remove_tool(stale_name)
            removed += 1
        if removed:
            logger.warning("Unregistered %d tools removed upstream", removed)

        return added, removed


def get_registered_tool_count() -> int:
    return len(_registered_tools)
