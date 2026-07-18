"""
sprout_registry/compiler/base.py
──────────────────────────────
Abstract base class for all framework adapters.

Every adapter receives a SproutTool and must return
a framework-native object. Nothing else is shared
between adapters — they're completely independent.

To add a new framework:
    1. Create sprout_registry/compiler/my_framework.py
    2. Subclass BaseAdapter
    3. Implement compile()
    4. Register in sprout_registry/runtime.py ADAPTERS dict

That's it. No changes to any other file.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from sprout_shared.spec import SproutTool


class BaseAdapter(ABC):
    """
    All framework adapters inherit from this.
    The only method you must implement is compile().
    """

    @property
    @abstractmethod
    def target(self) -> str:
        """Short name for this adapter, e.g. 'ag2', 'langchain', 'pydantic_ai'."""
        ...

    @abstractmethod
    def compile(self, tool: SproutTool) -> Any:
        """
        Convert a SproutTool into a native framework object.

        Args:
            tool: The SproutTool from the registry.

        Returns:
            Whatever the target framework expects.
            AG2    -> tuple(fn, description) ready for register_function
            LangChain -> StructuredTool instance
            Pydantic AI -> Tool instance
        """
        ...

    def _build_json_schema(self, tool: SproutTool) -> dict:
        """
        Helper: build a JSON Schema properties dict from tool params.
        Useful for any adapter that needs a schema (most of them).
        """
        properties = {}
        required = []

        for param in tool.spec.params:
            prop: dict[str, Any] = {"description": param.description}

            # type mapping
            type_map = {
                "str":   "string",
                "int":   "integer",
                "float": "number",
                "bool":  "boolean",
                "list":  "array",
                "dict":  "object",
                "any":   "string",   # fallback
            }
            prop["type"] = type_map.get(param.type, "string")

            if param.enum:
                prop["enum"] = param.enum

            if param.default is not None:
                prop["default"] = param.default

            properties[param.name] = prop

            if param.required:
                required.append(param.name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(target={self.target!r})"
