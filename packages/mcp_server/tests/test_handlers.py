"""Tests for the pure-function pieces of the MCP server.

The interesting bridge logic is the spec-to-schema translator and the
dynamic tool handler builder. Both are pure: no HTTP, no FastMCP server,
no clients required. The streaming/HTTP transport layer is exercised by
the Puppeteer e2e suite (Phase 4) once it's wired up.
"""

from __future__ import annotations

import asyncio

import pytest

from sprout_mcp import tools as tool_module
from sprout_mcp.tools import _build_mcp_tool_schema, _make_tool_handler

# ── _build_mcp_tool_schema ───────────────────────────────────────────────────


def test_empty_params_yields_empty_schema() -> None:
    schema = _build_mcp_tool_schema({"params": []})
    assert schema == {"type": "object", "properties": {}, "required": []}


def test_single_required_string_param() -> None:
    spec = {
        "params": [
            {"name": "city", "type": "str", "description": "City name", "required": True}
        ]
    }
    schema = _build_mcp_tool_schema(spec)
    assert schema["type"] == "object"
    assert schema["properties"] == {
        "city": {"type": "string", "description": "City name"}
    }
    assert schema["required"] == ["city"]


def test_sprout_type_to_jsonschema_type_mapping() -> None:
    """Every supported Sprout type must map to the right JSON Schema type."""
    spec = {
        "params": [
            {"name": "s", "type": "str"},
            {"name": "i", "type": "int"},
            {"name": "f", "type": "float"},
            {"name": "b", "type": "bool"},
            {"name": "l", "type": "list"},
            {"name": "d", "type": "dict"},
        ]
    }
    schema = _build_mcp_tool_schema(spec)
    assert schema["properties"]["s"]["type"] == "string"
    assert schema["properties"]["i"]["type"] == "integer"
    assert schema["properties"]["f"]["type"] == "number"
    assert schema["properties"]["b"]["type"] == "boolean"
    assert schema["properties"]["l"]["type"] == "array"
    assert schema["properties"]["d"]["type"] == "object"


def test_unknown_type_falls_back_to_string() -> None:
    """An unknown Sprout type must not crash — fall back to string."""
    spec = {"params": [{"name": "weird", "type": "totally_made_up"}]}
    schema = _build_mcp_tool_schema(spec)
    assert schema["properties"]["weird"]["type"] == "string"


def test_enum_param_includes_enum_in_schema() -> None:
    spec = {
        "params": [
            {"name": "size", "type": "str", "enum": ["small", "medium", "large"]}
        ]
    }
    schema = _build_mcp_tool_schema(spec)
    assert schema["properties"]["size"]["enum"] == ["small", "medium", "large"]


def test_default_value_is_propagated() -> None:
    spec = {
        "params": [
            {"name": "units", "type": "str", "default": "celsius"}
        ]
    }
    schema = _build_mcp_tool_schema(spec)
    assert schema["properties"]["units"]["default"] == "celsius"


def test_required_field_defaults_to_true_when_omitted() -> None:
    """When ``required`` is omitted, the param is treated as required.

    This matches the iter-23 prompt_builder convention and pins the
    "missing key = required" contract so a future change can't silently
    flip the default.
    """
    spec = {"params": [{"name": "p1", "type": "str"}]}
    schema = _build_mcp_tool_schema(spec)
    assert "p1" in schema["required"]


def test_optional_field_excluded_from_required_list() -> None:
    spec = {
        "params": [
            {"name": "name", "type": "str", "required": True},
            {"name": "nickname", "type": "str", "required": False},
        ]
    }
    schema = _build_mcp_tool_schema(spec)
    assert "name" in schema["required"]
    assert "nickname" not in schema["required"]


# ── _make_tool_handler ───────────────────────────────────────────────────────


def test_make_tool_handler_returns_named_async_callable() -> None:
    """The dynamically built handler must be an async function with the right name."""
    spec = {
        "name": "weather",
        "description": "Get the weather",
        "params": [{"name": "city", "type": "str", "required": True}],
    }
    handler = _make_tool_handler("com.sprout.tools.weather", spec)

    assert callable(handler)
    assert handler.__name__ == "weather"
    assert handler.__doc__ == "Get the weather"
    assert asyncio.iscoroutinefunction(handler)


def test_make_tool_handler_signature_matches_params() -> None:
    """The built function exposes the param names in its signature.

    FastMCP introspects the signature to generate the JSON schema, so a
    regression here would silently break tool discovery for MCP clients.
    """
    import inspect

    spec = {
        "name": "convert",
        "description": "Currency conversion",
        "params": [
            {"name": "amount", "type": "float", "required": True},
            {"name": "from_currency", "type": "str", "required": True},
            {"name": "to_currency", "type": "str", "required": False, "default": "USD"},
        ],
    }
    handler = _make_tool_handler("com.sprout.tools.convert", spec)
    sig = inspect.signature(handler)

    assert list(sig.parameters.keys()) == ["amount", "from_currency", "to_currency"]
    assert sig.parameters["to_currency"].default == "USD"


# ── sync_tools: stale tracking and name collisions ──────────────────────────


class _FakeMCP:
    """Minimal stand-in for FastMCP.tool() / remove_tool()."""

    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []
        self.removed: list[str] = []

    def tool(self, name: str, description: str = ""):
        def decorator(fn):
            self.registered.append((name, description))
            return fn
        return decorator

    def remove_tool(self, name: str) -> None:
        self.removed.append(name)


@pytest.fixture(autouse=True)
def reset_tool_state() -> None:
    tool_module._registered_tools.clear()
    tool_module._registered_names.clear()
    tool_module._stale_tool_ids.clear()


@pytest.mark.asyncio
async def test_sync_tools_adds_new_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch() -> list[dict]:
        return [{"id": "com.a", "name": "a", "description": "", "params": []}]

    monkeypatch.setattr(tool_module, "_fetch_tools", _fake_fetch)

    mcp = _FakeMCP()
    added, removed = await tool_module.sync_tools(mcp)

    assert added == 1
    assert removed == 0
    assert ("a", "") in mcp.registered


@pytest.mark.asyncio
async def test_sync_tools_rejects_name_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools_v1 = [{"id": "com.a.v1", "name": "shared", "description": "", "params": []}]
    tools_v2 = [{"id": "com.a.v2", "name": "shared", "description": "", "params": []}]

    async def _fake_fetch_v1() -> list[dict]:
        return tools_v1

    async def _fake_fetch_v2() -> list[dict]:
        return tools_v1 + tools_v2

    mcp = _FakeMCP()

    monkeypatch.setattr(tool_module, "_fetch_tools", _fake_fetch_v1)
    await tool_module.sync_tools(mcp)

    monkeypatch.setattr(tool_module, "_fetch_tools", _fake_fetch_v2)
    added, removed = await tool_module.sync_tools(mcp)

    assert added == 0
    assert removed == 0
    assert "com.a.v1" in tool_module._registered_tools
    assert "com.a.v2" not in tool_module._registered_tools


@pytest.mark.asyncio
async def test_sync_tools_marks_removed_tools_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools_initial = [{"id": "com.a", "name": "a", "description": "", "params": []}]

    async def _fake_fetch_initial() -> list[dict]:
        return tools_initial

    async def _fake_fetch_empty() -> list[dict]:
        return [{"id": "com.b", "name": "b", "description": "", "params": []}]

    mcp = _FakeMCP()

    monkeypatch.setattr(tool_module, "_fetch_tools", _fake_fetch_initial)
    await tool_module.sync_tools(mcp)
    assert "com.a" in tool_module._registered_tools

    monkeypatch.setattr(tool_module, "_fetch_tools", _fake_fetch_empty)
    added, removed = await tool_module.sync_tools(mcp)

    assert removed == 1
    assert "com.a" in tool_module._stale_tool_ids
    assert "com.a" not in tool_module._registered_tools
    assert "a" not in tool_module._registered_names
    assert "a" in mcp.removed, "stale tools must be unregistered from the MCP server"


@pytest.mark.asyncio
async def test_sync_tools_reregisters_previously_stale_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the stale-tool re-registration bug."""
    tools_initial = [{"id": "com.a", "name": "a", "description": "", "params": []}]

    async def _fake_fetch_with_a() -> list[dict]:
        return tools_initial

    async def _fake_fetch_other() -> list[dict]:
        return [{"id": "com.other", "name": "other", "description": "", "params": []}]

    mcp = _FakeMCP()

    monkeypatch.setattr(tool_module, "_fetch_tools", _fake_fetch_with_a)
    await tool_module.sync_tools(mcp)

    monkeypatch.setattr(tool_module, "_fetch_tools", _fake_fetch_other)
    await tool_module.sync_tools(mcp)
    assert "com.a" in tool_module._stale_tool_ids

    monkeypatch.setattr(tool_module, "_fetch_tools", _fake_fetch_with_a)
    added, _removed = await tool_module.sync_tools(mcp)

    assert added == 1
    assert "com.a" not in tool_module._stale_tool_ids
    assert "com.a" in tool_module._registered_tools


def test_make_tool_handler_rejects_invalid_tool_name() -> None:
    """exec injection guard: non-identifier name must raise."""
    spec = {"name": "foo()#evil", "description": "", "params": []}
    with pytest.raises(ValueError, match="Invalid tool name"):
        tool_module._make_tool_handler("com.evil", spec)


def test_make_tool_handler_rejects_invalid_param_name() -> None:
    """exec injection guard: non-identifier param name must raise."""
    spec = {
        "name": "safe_tool",
        "description": "",
        "params": [{"name": "bad;import os", "type": "str"}],
    }
    with pytest.raises(ValueError, match="Invalid param name"):
        tool_module._make_tool_handler("com.evil", spec)


def test_make_tool_handler_sanitizes_description() -> None:
    """Triple-quotes in description must not break the exec'd docstring."""
    spec = {
        "name": "safe_tool",
        "description": 'has """ triple quotes""" inside',
        "params": [],
    }
    handler = tool_module._make_tool_handler("com.safe", spec)
    assert callable(handler)
    assert '"""' not in (handler.__doc__ or "")


@pytest.mark.asyncio
async def test_execute_tool_safe_injects_user_id_from_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that when an auth context is present, user_id flows to _execute_tool."""
    from types import SimpleNamespace

    fake_token = SimpleNamespace(user_id="user_ctx_test")

    def _fake_get_token() -> SimpleNamespace:
        return fake_token

    monkeypatch.setattr(tool_module, "get_access_token", _fake_get_token)

    captured = {}

    async def _fake_execute(tool_id: str, args: dict, *, user_id: str | None = None) -> dict:
        captured["tool_id"] = tool_id
        captured["user_id"] = user_id
        return {"success": True, "result": "ok"}

    monkeypatch.setattr(tool_module, "_execute_tool", _fake_execute)

    await tool_module._execute_tool_safe("com.test", {"x": 1})

    assert captured["tool_id"] == "com.test"
    assert captured["user_id"] == "user_ctx_test"


@pytest.mark.asyncio
async def test_execute_tool_safe_no_user_id_when_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no auth context is present, user_id stays None."""

    def _no_token() -> None:
        return None

    monkeypatch.setattr(tool_module, "get_access_token", _no_token)

    captured = {}

    async def _fake_execute(tool_id: str, args: dict, *, user_id: str | None = None) -> dict:
        captured["user_id"] = user_id
        return {"success": True, "result": "ok"}

    monkeypatch.setattr(tool_module, "_execute_tool", _fake_execute)

    await tool_module._execute_tool_safe("com.test", {})

    assert captured["user_id"] is None
