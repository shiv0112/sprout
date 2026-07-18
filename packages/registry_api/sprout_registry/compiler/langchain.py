"""
sprout_registry/compiler/langchain.py
───────────────────────────────────
Compiles a SproutTool into a LangChain StructuredTool.

LangChain expects:
    - StructuredTool with name, description, func, args_schema
    - args_schema is a Pydantic BaseModel class

We dynamically build the Pydantic model from the SproutToolSpec params,
so you get full type validation and schema generation for free.

Usage:
    compiled = runtime.get("com.sprout.tools.weather", target="langchain")
    # compiled is a LangChain StructuredTool, ready to use

    # In an agent:
    agent = initialize_agent(tools=[compiled], llm=llm, ...)

    # Or call directly:
    result = compiled.run({"location": "Singapore", "units": "celsius"})
"""

from __future__ import annotations

from typing import Any

from sprout_shared.spec import SproutTool, ToolParam

from .base import BaseAdapter


class LangChainAdapter(BaseAdapter):

    @property
    def target(self) -> str:
        return "langchain"

    def compile(self, tool: SproutTool) -> Any:
        """
        Returns a LangChain StructuredTool.
        The args_schema is dynamically built from the SproutToolSpec.
        """
        try:
            # langchain-core is an optional dep; only used if a tool author
            # asks for the langchain compile target. mypy can't see the stub.
            from langchain_core.tools import StructuredTool  # type: ignore[import-not-found]
        except ImportError:
            raise ImportError(
                "LangChain not installed. Run: pip install langchain langchain-core"
            ) from None

        spec = tool.spec
        args_schema = _build_pydantic_model(spec.name, spec.params)

        def _invoke(**kwargs) -> Any:
            return tool.fn(**kwargs)

        structured_tool = StructuredTool.from_function(
            func=_invoke,
            name=spec.name,
            description=spec.description,
            args_schema=args_schema,
            return_direct=False,
        )

        # Compiled successfully
        return structured_tool


def _build_pydantic_model(model_name: str, params: list[ToolParam]) -> type:
    """
    Dynamically build a Pydantic v2 BaseModel class from a list of ToolParams.

    This is the key piece — LangChain uses the Pydantic schema to:
    1. Validate inputs before calling the tool
    2. Generate the JSON schema it sends to the LLM
    """

    from pydantic import Field, create_model

    # Map Sprout type strings -> Python types
    type_map = {
        "str":   str,
        "int":   int,
        "float": float,
        "bool":  bool,
        "list":  list,
        "dict":  dict,
        "any":   Any,
    }

    field_definitions: dict[str, Any] = {}

    for param in params:
        # `python_type` may be either a real `type` (str, int, ...) or a
        # typing special form like `Literal["a","b"]`, so annotate as Any.
        python_type: Any = type_map.get(param.type, str)

        # Handle enums — use Literal type for strict validation
        if param.enum:
            from typing import Literal
            # Dynamically build Literal["a", "b", "c"]
            literal_type = Literal[tuple(param.enum)]  # type: ignore
            python_type = literal_type

        field_kwargs: dict[str, Any] = {
            "description": param.description,
        }
        if param.default is not None:
            field_kwargs["default"] = param.default

        if not param.required:
            # Make the field optional
            if param.default is not None:
                field_definitions[param.name] = (python_type, Field(param.default, **{k: v for k, v in field_kwargs.items() if k != "default"}))
            else:
                field_definitions[param.name] = (python_type | None, Field(None, description=param.description))
        else:
            field_definitions[param.name] = (python_type, Field(..., description=param.description))

    # Dynamically create the Pydantic model
    model = create_model(
        f"{model_name.title().replace('_', '')}Input",
        **field_definitions,
    )
    return model
