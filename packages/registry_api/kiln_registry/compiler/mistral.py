"""
kiln_registry/compiler/mistral.py
──────────────────────────────────
Compiles a KilnTool into Mistral-compatible tool definitions.

Mistral's function-calling API expects tools in this shape:

    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "...",
            "parameters": { <JSON Schema> }
        }
    }

We return a CompiledMistralTool that carries both the tool definition
dict (for the API call) and the callable (for local execution).

Usage:
    compiled = runtime.get("com.kiln.tools.weather", target="mistral")

    # Pass the definition to Mistral
    response = client.chat.complete(
        model="mistral-large-latest",
        messages=messages,
        tools=[compiled.tool_def],
    )

    # Execute when Mistral calls back
    result = compiled.call({"location": "Singapore", "units": "celsius"})
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kiln_shared.spec import KilnTool

from .base import BaseAdapter


@dataclass
class CompiledMistralTool:
    """
    Everything Mistral needs, in one object.

    Attributes:
        name      - function name used for dispatch
        description - shown to the model (not strictly needed here,
                      it's already inside tool_def)
        fn        - the actual Python callable
        tool_def  - the dict to pass inside tools=[...] in the API call
    """
    name: str
    description: str
    fn: Callable
    tool_def: dict

    def call(self, arguments: dict | str) -> Any:
        """
        Invoke the tool.

        Mistral returns arguments as a JSON string; we handle both
        a pre-parsed dict and a raw JSON string for convenience.
        """
        # Use a fresh local so mypy can narrow it cleanly to dict.
        kwargs: dict = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(kwargs, dict):
            raise TypeError(
                f"Mistral tool arguments must be a JSON object, got {type(kwargs).__name__}"
            )
        return self.fn(**kwargs)


class MistralAdapter(BaseAdapter):

    @property
    def target(self) -> str:
        return "mistral"

    def compile(self, tool: KilnTool) -> CompiledMistralTool:
        """
        Build the Mistral tool definition dict from the KilnToolSpec
        and wrap the callable for easy dispatch.
        """
        spec = tool.spec

        tool_def = {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": self._build_json_schema(tool),
            },
        }

        return CompiledMistralTool(
            name=spec.name,
            description=spec.description,
            fn=tool.fn,
            tool_def=tool_def,
        )
