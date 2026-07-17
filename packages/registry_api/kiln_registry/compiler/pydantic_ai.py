"""
kiln_registry/compiler/pydantic_ai.py
─────────────────────────────
Compiles a KilnTool into a format usable with Pydantic AI.

Pydantic AI tools work in two ways:
    1. As a decorated function passed to Agent(tools=[...])
    2. As a Tool() object with explicit schema

We use approach 2 — build a typed wrapper function with proper
annotations so Pydantic AI can introspect the schema directly.

Usage:
    compiled = runtime.get("com.kiln.tools.weather", target="pydantic_ai")
    agent = Agent("openai:gpt-4o", tools=[compiled])

    Or access the raw wrapped function:
    compiled_fn = runtime.get("com.kiln.tools.weather", target="pydantic_ai")
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kiln_shared.spec import KilnTool

from .base import BaseAdapter


@dataclass
class CompiledPydanticAITool:
    """
    Wraps the compiled function alongside metadata.
    Use .fn directly with Pydantic AI's Agent(tools=[...]).
    """
    name: str
    description: str
    fn: Callable
    schema: dict

    def as_tool(self) -> Any:
        """
        Return as a pydantic_ai.Tool object if pydantic_ai is installed.
        Falls back to returning the raw function if not.
        """
        try:
            # pydantic_ai is an optional compile target — only used if a tool
            # author asks for it. mypy can't see the stub when not installed.
            from pydantic_ai import Tool  # type: ignore[import-not-found]
            return Tool(self.fn, description=self.description)
        except ImportError:
            # pydantic_ai not installed — return the typed function
            # which pydantic_ai can also accept directly
            return self.fn


class PydanticAIAdapter(BaseAdapter):

    @property
    def target(self) -> str:
        return "pydantic_ai"

    def compile(self, tool: KilnTool) -> CompiledPydanticAITool:
        """
        Build a typed wrapper function that Pydantic AI can introspect.
        Pydantic AI uses the function's type annotations to build its schema,
        so we dynamically construct a properly-annotated function.
        """
        spec = tool.spec
        fn = _make_pydantic_ai_wrapper(tool)

        return CompiledPydanticAITool(
            name=spec.name,
            description=spec.description,
            fn=fn,
            schema=self._build_json_schema(tool),
        )


def _make_pydantic_ai_wrapper(tool: KilnTool) -> Callable:
    """
    Build a typed wrapper with full annotations.
    Pydantic AI reflects on __annotations__ and __doc__ to generate
    the schema it sends to the model — we must provide both.
    """
    spec = tool.spec
    params = spec.params

    type_map = {
        "str":   "str",
        "int":   "int",
        "float": "float",
        "bool":  "bool",
        "list":  "list",
        "dict":  "dict",
        "any":   "str",
    }

    # Build signature with full type annotations
    sig_parts = []
    for p in params:
        type_str = type_map.get(p.type, "str")
        if not p.required and p.default is not None:
            sig_parts.append(f"{p.name}: {type_str} = {repr(p.default)}")
        elif not p.required:
            sig_parts.append(f"{p.name}: {type_str} = None")
        else:
            sig_parts.append(f"{p.name}: {type_str}")

    sig_str = ", ".join(sig_parts)
    kwargs_str = ", ".join(f"{p.name}={p.name}" for p in params)

    # Build docstring with param descriptions — Pydantic AI uses this
    param_docs = "\n".join(
        f"    {p.name}: {p.description}" for p in params if p.description
    )
    docstring = f"{spec.description}\n\nArgs:\n{param_docs}"

    fn_source = f"""
def {spec.name}({sig_str}):
    \"\"\"{docstring}\"\"\"
    return _original_fn({kwargs_str})
"""

    namespace = {"_original_fn": tool.fn}
    exec(fn_source, namespace)
    wrapper = namespace[spec.name]

    return wrapper
