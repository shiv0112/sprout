"""
kiln_registry/registry.py
─────────────────────────
The Kiln registry. Stores KilnTool objects by ID.
In-memory for now (hackathon scope).
Can be swapped for SQLite or a remote registry later
without touching any other module.

The registry is the single source of truth.
Adapters never store tools themselves.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from kiln_shared.spec import KilnTool

from .sqlite_registry import SQLiteRegistry


@runtime_checkable
class RegistryProtocol(Protocol):
    """Public surface every Kiln registry backend must expose.

    Both ``KilnRegistry`` (in-memory) and ``SQLiteRegistry`` (persistent)
    satisfy this Protocol via structural typing — no inheritance required.
    Use this in function signatures (e.g. ``get_global_registry()``) so
    the registry implementation can be swapped without churn.
    """

    def register(self, tool: KilnTool) -> None: ...
    def unregister(self, tool_id: str) -> None: ...
    def get(self, tool_id: str) -> KilnTool | None: ...
    def query(self, name: str) -> KilnTool | None: ...
    def has(self, tool_id: str) -> bool: ...
    def list_all(self) -> list[KilnTool]: ...
    def list_ids(self) -> list[str]: ...
    def by_category(self, category: str) -> list[KilnTool]: ...
    def by_tag(self, tag: str) -> list[KilnTool]: ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[KilnTool]: ...

logger = logging.getLogger(__name__)


class KilnRegistry:
    """
    In-memory store of KilnTools, keyed by tool ID.

    Usage:
        registry = KilnRegistry()
        registry.register(my_tool)
        tool = registry.get("com.kiln.tools.weather")
        all_tools = registry.list_all()
    """

    def __init__(self):
        self._tools: dict[str, KilnTool] = {}

    # ── Write ─────────────────────────────────────────────────────────────────

    def register(self, tool: KilnTool) -> None:
        """Register a KilnTool. Overwrites if same ID exists."""
        self._tools[tool.id] = tool
        logger.info(f"Registered: {tool.id} ({tool.spec.name})")

    def unregister(self, tool_id: str) -> None:
        """Remove a tool from the registry."""
        if tool_id in self._tools:
            del self._tools[tool_id]

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, tool_id: str) -> KilnTool | None:
        """Return the KilnTool or None if not found."""
        return self._tools.get(tool_id)

    def query(self, name: str) -> KilnTool | None:
        """
        Fuzzy lookup by tool name (not full ID).
        Useful when the planner only knows the short name.
        """
        # exact name match first
        for tool in self._tools.values():
            if tool.spec.name == name:
                return tool
        # partial ID match
        name_lower = name.lower()
        for tool_id, tool in self._tools.items():
            if name_lower in tool_id.lower():
                return tool
        return None

    def has(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def list_all(self) -> list[KilnTool]:
        return list(self._tools.values())

    def list_ids(self) -> list[str]:
        return list(self._tools.keys())

    def by_category(self, category: str) -> list[KilnTool]:
        return [t for t in self._tools.values() if t.spec.category == category]

    def by_tag(self, tag: str) -> list[KilnTool]:
        return [t for t in self._tools.values() if tag in t.spec.tags]

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[KilnTool]:
        return iter(self._tools.values())

    def __repr__(self) -> str:
        return f"KilnRegistry({len(self._tools)} tools: {self.list_ids()})"

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """Human-readable catalog for debugging / demo."""
        if not self._tools:
            return "Registry is empty."
        lines = ["┌─ Kiln Registry ─────────────────────────────────┐"]
        for tool in self._tools.values():
            s = tool.spec
            lines.append(f"│  {s.id:<42} v{s.version}")
            lines.append(f"│    {s.description[:60]}")
            lines.append("│")
        lines.append("└─────────────────────────────────────────────────┘")
        return "\n".join(lines)


# ── Global singleton registry ─────────────────────────────────────────────────
# Tools decorated with @kiln_tool can auto-register here.
# Or you can create your own registry instance for isolation.

_global_registry: RegistryProtocol = SQLiteRegistry()


def get_global_registry() -> RegistryProtocol:
    return _global_registry


def register(tool: KilnTool) -> KilnTool:
    """Register a tool in the global registry. Returns the tool for chaining."""
    _global_registry.register(tool)
    return tool
