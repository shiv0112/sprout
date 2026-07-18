"""
sprout/spec.py
─────────────
The heart of Sprout. A SproutTool is a plain Python function
plus a SproutToolSpec that describes it in a framework-agnostic way.

Nothing here imports AG2, LangChain, or Pydantic AI.
This module must stay framework-free forever.
"""

from __future__ import annotations

import builtins
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

# ── Parameter & Return types ──────────────────────────────────────────────────

SUPPORTED_TYPES = {
    "str":   str,
    "int":   int,
    "float": float,
    "bool":  bool,
    "list":  list,
    "dict":  dict,
    "any":   Any,
}


@dataclass
class ToolParam:
    """One input parameter of a Sprout tool."""
    name: str
    type: str                    # "str" | "int" | "float" | "bool" | "list" | "dict"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: list[str] | None = None   # for string enums

    def python_type(self) -> builtins.type:
        # Use builtins.type because the `type` field above shadows the builtin
        # inside class scope, which mypy resolves through forward refs.
        return SUPPORTED_TYPES.get(self.type, Any)


@dataclass
class ToolReturn:
    """Return spec of a Sprout tool."""
    type: str = "dict"
    description: str = ""


# ── The Spec ──────────────────────────────────────────────────────────────────

@dataclass
class SproutToolSpec:
    """
    Framework-agnostic description of a tool.
    Every adapter reads from this — nothing else.
    """
    id: str                             # e.g. "com.sprout.tools.weather"
    name: str                           # function name, e.g. "get_weather"
    description: str                    # shown to the LLM — make it crisp
    params: list[ToolParam] = field(default_factory=list)
    returns: ToolReturn = field(default_factory=ToolReturn)
    version: str = "1.0.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = "general"
    required_env_vars: list[str] = field(default_factory=list)


# ── The Tool (spec + implementation) ─────────────────────────────────────────

@dataclass
class SproutTool:
    """
    A registered Sprout tool: spec metadata + the actual callable.
    This is what lives in the registry and what adapters receive.
    """
    spec: SproutToolSpec
    fn: Callable

    def __call__(self, **kwargs) -> Any:
        return self.fn(**kwargs)

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def name(self) -> str:
        return self.spec.name


# ── @sprout_tool decorator ─────────────────────────────────────────────────────

def sprout_tool(
    id: str,
    description: str,
    version: str = "1.0.0",
    author: str = "",
    tags: list[str] | None = None,
    category: str = "general",
    param_descriptions: dict[str, str] | None = None,
    param_enums: dict[str, list[str]] | None = None,
):
    """
    Decorator that wraps a plain Python function as a SproutTool.

    Usage:
        @sprout_tool(
            id="com.sprout.tools.weather",
            description="Get current weather for a city.",
            param_descriptions={"location": "City name", "units": "celsius or fahrenheit"},
            param_enums={"units": ["celsius", "fahrenheit"]},
        )
        def get_weather(location: str, units: str = "celsius") -> dict:
            ...

    The decorator introspects the function signature to build ToolParams
    automatically — you never write the schema by hand.
    """
    tags = tags or []
    param_descriptions = param_descriptions or {}
    param_enums = param_enums or {}

    def decorator(fn: Callable) -> SproutTool:
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)

        params: list[ToolParam] = []
        for param_name, param in sig.parameters.items():
            python_type = hints.get(param_name, Any)
            type_str = _python_type_to_str(python_type)
            has_default = param.default is not inspect.Parameter.empty
            default_val = param.default if has_default else None

            params.append(ToolParam(
                name=param_name,
                type=type_str,
                description=param_descriptions.get(param_name, ""),
                required=not has_default,
                default=default_val,
                enum=param_enums.get(param_name),
            ))

        # return type
        return_hint = hints.get("return", dict)
        return_type_str = _python_type_to_str(return_hint)

        spec = SproutToolSpec(
            id=id,
            name=fn.__name__,
            description=description,
            params=params,
            returns=ToolReturn(type=return_type_str),
            version=version,
            author=author,
            tags=tags,
            category=category,
        )

        tool = SproutTool(spec=spec, fn=fn)
        return tool

    return decorator


# ── helpers ───────────────────────────────────────────────────────────────────

def _python_type_to_str(t: Any) -> str:
    """Map Python type → Sprout type string."""
    mapping = {
        str: "str",
        int: "int",
        float: "float",
        bool: "bool",
        list: "list",
        dict: "dict",
    }
    # Handle typing generics like list[str], dict[str, Any]
    origin = getattr(t, "__origin__", None)
    if origin is list:
        return "list"
    if origin is dict:
        return "dict"
    return mapping.get(t, "any")
