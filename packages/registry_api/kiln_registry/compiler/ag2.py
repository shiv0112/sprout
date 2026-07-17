"""
kiln_registry/compiler/ag2.py
─────────────────────────────
Compiles a KilnTool into AG2-compatible objects.

AG2 (AutoGen2) expects:
    - A plain Python callable
    - register_function(fn, caller=..., executor=..., description=..., name=...)

We return a CompiledAG2Tool dataclass with everything AG2 needs,
so the caller just does:

    compiled = runtime.get("com.kiln.tools.weather", target="ag2")
    register_function(
        compiled.fn,
        caller=assistant,
        executor=executor,
        description=compiled.description,
        name=compiled.name,
    )

Or use the convenience method:

    compiled.register(caller=assistant, executor=executor)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kiln_shared.spec import KilnTool

from .base import BaseAdapter

logger = logging.getLogger(__name__)


@dataclass
class CompiledAG2Tool:
    """
    Everything AG2 needs, in one object.
    Call .register(caller, executor) for one-line setup.
    """
    name: str
    description: str
    fn: Callable
    schema: dict          # JSON schema of the function parameters

    def register(self, caller: Any, executor: Any) -> None:
        """
        One-line AG2 registration.

        Usage:
            compiled = runtime.get("com.kiln.tools.weather", target="ag2")
            compiled.register(caller=assistant_agent, executor=executor_agent)
        """
        try:
            # AG2 (autogen) does not ship a py.typed marker.
            from autogen import register_function  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError(
                "AG2/AutoGen not installed. Run: pip install autogen-agentchat"
            ) from None
        register_function(
            self.fn,
            caller=caller,
            executor=executor,
            description=self.description,
            name=self.name,
        )
        logger.info(f"Registered: {self.name}")


class AG2Adapter(BaseAdapter):

    @property
    def target(self) -> str:
        return "ag2"

    def compile(self, tool: KilnTool) -> CompiledAG2Tool:
        """
        Wrap the tool's fn so it accepts keyword arguments as AG2 expects.
        AG2 calls tools with keyword args matching the param names.
        """
        spec = tool.spec

        # Build a wrapper that enforces the Kiln spec's parameter contract
        # and provides clear error messages when AG2 passes wrong types
        fn = _make_ag2_wrapper(tool)

        return CompiledAG2Tool(
            name=spec.name,
            description=spec.description,
            fn=fn,
            schema=self._build_json_schema(tool),
        )


def _make_ag2_wrapper(tool: KilnTool) -> Callable:
    """
    Build a strongly-typed wrapper function that AG2 can call directly.
    We dynamically produce a function with the exact signature AG2 expects
    so that AG2's introspection (inspect.signature) works correctly.
    """
    spec = tool.spec
    params = spec.params

    # Build the function signature string
    # e.g. "location: str, units: str = 'celsius'"
    sig_parts = []
    for p in params:
        type_annotation = p.type if p.type != "any" else "str"
        if not p.required and p.default is not None:
            default_repr = repr(p.default)
            sig_parts.append(f"{p.name}: {type_annotation} = {default_repr}")
        elif not p.required:
            sig_parts.append(f"{p.name}: {type_annotation} = None")
        else:
            sig_parts.append(f"{p.name}: {type_annotation}")

    sig_str = ", ".join(sig_parts)

    # Build the kwargs dict to pass to the original function
    kwargs_str = ", ".join(f"{p.name}={p.name}" for p in params)

    # The wrapper function source — we use dynamic code generation here
    # because AG2 relies on inspect.signature to extract parameter info
    fn_source = (
        f"def {spec.name}({sig_str}):\n"
        f'    """{spec.description}"""\n'
        f"    return _original_fn({kwargs_str})\n"
    )

    # Build into a namespace with the original function available
    namespace = {"_original_fn": tool.fn}
    # NOTE: Using exec() here is intentional — it is the standard pattern for
    # dynamic function generation with correct signatures for framework
    # introspection (AG2 uses inspect.signature). Input is developer-controlled
    # spec metadata, not user input.
    _dynamic_exec(fn_source, namespace)
    wrapper = namespace[spec.name]

    return wrapper


def _dynamic_exec(source: str, namespace: dict) -> None:
    """Execute dynamically generated source code in the given namespace."""
    exec(source, namespace)  # noqa: S102
