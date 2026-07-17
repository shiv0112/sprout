"""
kiln_registry/runtime.py
────────────────────────
KilnRuntime: the main entry point for consuming tools.

This is what your agentic system talks to.
It doesn't care about frameworks — it just hands you
a native object for whatever framework you specify.

Usage:
    from kiln_registry.runtime import KilnRuntime

    runtime = KilnRuntime(target="ag2")

    # Load a tool — returns native AG2 CompiledAG2Tool
    tool = runtime.get("com.kiln.tools.weather")
    tool.register(caller=assistant, executor=executor)

    # Switch target on the fly
    lc_tool = runtime.get("com.kiln.tools.weather", target="langchain")

    # Load all registered tools at once
    all_tools = runtime.get_all(target="langchain")
"""

from __future__ import annotations

import logging
from typing import Any

from kiln_shared.spec import KilnTool

from .compiler.ag2 import AG2Adapter
from .compiler.base import BaseAdapter
from .compiler.langchain import LangChainAdapter
from .compiler.mistral import MistralAdapter
from .compiler.pydantic_ai import PydanticAIAdapter
from .registry import KilnRegistry, get_global_registry

logger = logging.getLogger(__name__)

# ── Adapter registry ──────────────────────────────────────────────────────────
# Add new framework adapters here — nowhere else.

ADAPTERS: dict[str, BaseAdapter] = {
    "ag2":         AG2Adapter(),
    "langchain":   LangChainAdapter(),
    "pydantic_ai": PydanticAIAdapter(),
    "mistral":     MistralAdapter(),
}


class KilnRuntime:
    """
    Loads tools from a KilnRegistry and compiles them
    for a target framework on demand.

    Results are cached — compiling the same tool twice
    for the same target returns the cached object.
    """

    def __init__(
        self,
        target: str = "ag2",
        registry: KilnRegistry | None = None,
    ):
        if target not in ADAPTERS:
            raise ValueError(
                f"Unknown target '{target}'. "
                f"Available: {list(ADAPTERS.keys())}"
            )
        self._default_target = target
        self._registry = registry or get_global_registry()
        self._cache: dict[str, dict[str, Any]] = {}   # {tool_id: {target: compiled}}

    # ── Core API ──────────────────────────────────────────────────────────────

    def get(self, tool_id: str, target: str | None = None) -> Any:
        """
        Get a compiled tool by ID for the specified target framework.

        Args:
            tool_id: Full tool ID, e.g. "com.kiln.tools.weather"
                     OR short name, e.g. "weather" (fuzzy match)
            target:  Framework target. Defaults to the runtime's default.

        Returns:
            Framework-native tool object:
                ag2          -> CompiledAG2Tool
                langchain    -> StructuredTool
                pydantic_ai  -> CompiledPydanticAITool

        Raises:
            KeyError if tool not found in registry.
            ValueError if target is unknown.
        """
        target = target or self._default_target
        _validate_target(target)

        # Resolve the tool from registry (try exact ID first, then fuzzy)
        kiln_tool = self._registry.get(tool_id)
        if kiln_tool is None:
            kiln_tool = self._registry.query(tool_id)
        if kiln_tool is None:
            raise KeyError(
                f"Tool '{tool_id}' not found in registry. "
                f"Available: {self._registry.list_ids()}"
            )

        return self._compile(kiln_tool, target)

    def get_all(self, target: str | None = None) -> list[Any]:
        """
        Get all registered tools compiled for the target framework.
        Useful for passing to an agent that accepts a list of tools.
        """
        target = target or self._default_target
        _validate_target(target)
        return [self._compile(tool, target) for tool in self._registry.list_all()]

    def get_many(self, tool_ids: list[str], target: str | None = None) -> list[Any]:
        """
        Get specific tools by ID, compiled for the target framework.
        """
        return [self.get(tid, target) for tid in tool_ids]

    # ── Cache-aware compilation ───────────────────────────────────────────────

    def _compile(self, tool: KilnTool, target: str) -> Any:
        """Compile with caching — same tool + target = same object."""
        cache_key = f"{tool.id}::{target}"
        if cache_key not in self._cache:
            adapter = ADAPTERS[target]
            compiled = adapter.compile(tool)
            self._cache[cache_key] = compiled
            logger.info(f"Compiled {tool.id} -> {target}")
        return self._cache[cache_key]

    def invalidate_cache(self, tool_id: str | None = None) -> None:
        """Clear cache for a specific tool or all tools."""
        if tool_id:
            keys_to_remove = [k for k in self._cache if k.startswith(tool_id)]
            for k in keys_to_remove:
                del self._cache[k]
        else:
            self._cache.clear()

    # ── Convenience ───────────────────────────────────────────────────────────

    def available_targets(self) -> list[str]:
        return list(ADAPTERS.keys())

    def __repr__(self) -> str:
        return (
            f"KilnRuntime("
            f"target={self._default_target!r}, "
            f"tools={len(self._registry)}, "
            f"cached={len(self._cache)})"
        )


# ── Helper ────────────────────────────────────────────────────────────────────

def _validate_target(target: str) -> None:
    if target not in ADAPTERS:
        raise ValueError(
            f"Unknown target '{target}'. "
            f"Available targets: {list(ADAPTERS.keys())}"
        )
