"""Tests for sprout_mcp.creation: the LLM-client-driven tool authoring path.

These cover input validation only — submit_to_registry is exercised by the
end-to-end Puppeteer suite, since it needs a live registry plus the internal
secret. We deliberately avoid mocking httpx here to keep the test surface
honest: a passing test should mean the validator actually protects the
registry from malformed specs.
"""

from __future__ import annotations

import pytest
import yaml

from sprout_mcp.creation import (
    ToolCreationError,
    build_spec_yaml,
    validate_impl_defines_function,
)


def test_build_spec_yaml_round_trips_a_valid_tool() -> None:
    yaml_str = build_spec_yaml(
        tool_id="com.sprout.tools.sample",
        name="sample",
        description="A sample tool",
        params=[
            {"name": "query", "type": "str", "description": "Query", "required": True},
            {"name": "limit", "type": "int", "description": "Limit", "required": False, "default": 10},
        ],
        returns={"type": "dict", "description": "Results"},
        dependencies=["requests>=2.28"],
        version="1.0.0",
        author="tester",
        tags=["sample", "test"],
        category="data",
    )

    parsed = yaml.safe_load(yaml_str)

    assert parsed["sprout_version"] == "1.0"
    assert parsed["tool"]["id"] == "com.sprout.tools.sample"
    assert parsed["tool"]["name"] == "sample"
    assert parsed["tool"]["version"] == "1.0.0"
    assert parsed["implementation"]["entrypoint"] == "sample.py"
    assert parsed["implementation"]["dependencies"] == ["requests>=2.28"]
    assert parsed["metadata"]["generated_by"] == "mcp_client"
    assert parsed["metadata"]["tags"] == ["sample", "test"]

    inputs = parsed["interface"]["inputs"]
    assert inputs[0] == {
        "name": "query",
        "type": "string",
        "description": "Query",
        "required": True,
    }
    assert inputs[1] == {
        "name": "limit",
        "type": "integer",
        "description": "Limit",
        "required": False,
        "default": 10,
    }

    outputs = parsed["interface"]["outputs"]
    assert outputs == [{"name": "result", "type": "object", "description": "Results"}]


def test_build_spec_yaml_rejects_bad_tool_id() -> None:
    with pytest.raises(ToolCreationError, match="tool_id"):
        build_spec_yaml(
            tool_id="not-a-dotted-id",
            name="sample",
            description="a sufficiently descriptive sentence",
            params=[],
            returns=None,
            dependencies=None,
            version="1.0.0",
            author="t",
            tags=None,
            category="general",
        )


def test_build_spec_yaml_rejects_bad_function_name() -> None:
    with pytest.raises(ToolCreationError, match="identifier"):
        build_spec_yaml(
            tool_id="com.sprout.tools.sample",
            name="bad name",
            description="a sufficiently descriptive sentence",
            params=[],
            returns=None,
            dependencies=None,
            version="1.0.0",
            author="t",
            tags=None,
            category="general",
        )


def test_build_spec_yaml_rejects_short_description() -> None:
    with pytest.raises(ToolCreationError, match="10 characters"):
        build_spec_yaml(
            tool_id="com.sprout.tools.sample",
            name="sample",
            description="too short",
            params=[],
            returns=None,
            dependencies=None,
            version="1.0.0",
            author="t",
            tags=None,
            category="general",
        )


def test_build_spec_yaml_rejects_whitespace_only_description() -> None:
    with pytest.raises(ToolCreationError, match="10 characters"):
        build_spec_yaml(
            tool_id="com.sprout.tools.sample",
            name="sample",
            description="   ",
            params=[],
            returns=None,
            dependencies=None,
            version="1.0.0",
            author="t",
            tags=None,
            category="general",
        )


def test_build_spec_yaml_rejects_duplicate_param_names() -> None:
    with pytest.raises(ToolCreationError, match="duplicate"):
        build_spec_yaml(
            tool_id="com.sprout.tools.sample",
            name="sample",
            description="a sufficiently descriptive sentence",
            params=[
                {"name": "q", "type": "str"},
                {"name": "q", "type": "int"},
            ],
            returns=None,
            dependencies=None,
            version="1.0.0",
            author="t",
            tags=None,
            category="general",
        )


def test_build_spec_yaml_rejects_unsupported_param_type() -> None:
    with pytest.raises(ToolCreationError, match="type must be one of"):
        build_spec_yaml(
            tool_id="com.sprout.tools.sample",
            name="sample",
            description="a sufficiently descriptive sentence",
            params=[{"name": "q", "type": "tuple"}],
            returns=None,
            dependencies=None,
            version="1.0.0",
            author="t",
            tags=None,
            category="general",
        )


def test_build_spec_yaml_accepts_python_and_json_schema_types() -> None:
    """LLM clients may pass either `str` or `string`; both should work."""
    yaml_str = build_spec_yaml(
        tool_id="com.sprout.tools.alias",
        name="alias",
        description="Exercises type aliasing between Python and JSON schema",
        params=[
            {"name": "a", "type": "str"},
            {"name": "b", "type": "string"},
            {"name": "c", "type": "int"},
            {"name": "d", "type": "integer"},
        ],
        returns={"type": "dict"},
        dependencies=None,
        version="1.0.0",
        author="t",
        tags=None,
        category="general",
    )
    parsed = yaml.safe_load(yaml_str)
    types = [p["type"] for p in parsed["interface"]["inputs"]]
    assert types == ["string", "string", "integer", "integer"]
    assert parsed["interface"]["outputs"][0]["type"] == "object"


def test_build_spec_yaml_rejects_required_as_non_boolean() -> None:
    """Strings like 'false' are truthy in Python — coercing them with bool()
    would silently flip an "optional" param into "required". Reject instead.
    """
    for bad_value in ["false", "true", 0, 1, "no", None]:
        with pytest.raises(ToolCreationError, match="required must be a boolean"):
            build_spec_yaml(
                tool_id="com.sprout.tools.sample",
                name="sample",
                description="a sufficiently descriptive sentence",
                params=[{"name": "q", "type": "str", "required": bad_value}],
                returns=None,
                dependencies=None,
                version="1.0.0",
                author="t",
                tags=None,
                category="general",
            )


def test_build_spec_yaml_rejects_param_with_non_identifier_name() -> None:
    with pytest.raises(ToolCreationError, match="python identifier"):
        build_spec_yaml(
            tool_id="com.sprout.tools.sample",
            name="sample",
            description="a sufficiently descriptive sentence",
            params=[{"name": "bad name", "type": "str"}],
            returns=None,
            dependencies=None,
            version="1.0.0",
            author="t",
            tags=None,
            category="general",
        )


def test_validate_impl_accepts_sync_def() -> None:
    validate_impl_defines_function("def sample(q: str) -> dict:\n    return {}", "sample")


def test_validate_impl_accepts_async_def() -> None:
    validate_impl_defines_function(
        "async def sample(q: str) -> dict:\n    return {}", "sample"
    )


def test_validate_impl_rejects_missing_function() -> None:
    with pytest.raises(ToolCreationError, match="must define a top-level function"):
        validate_impl_defines_function("x = 1\ny = 2\n", "sample")


def test_validate_impl_rejects_wrong_function_name() -> None:
    with pytest.raises(ToolCreationError):
        validate_impl_defines_function("def other():\n    pass\n", "sample")


def test_validate_impl_rejects_empty_code() -> None:
    with pytest.raises(ToolCreationError, match="must not be empty"):
        validate_impl_defines_function("   ", "sample")


def test_validate_impl_rejects_indented_def_of_matching_name() -> None:
    """A class method with the right name but no top-level function must fail.

    Regression guard: the previous regex allowed `\\s*` before `def`, so
    class methods would sneak past validation, and the registry would
    reject the import at test-fixture time after a pointless round-trip.
    """
    class_only = (
        "class Container:\n"
        "    def sample(self, q: str) -> dict:\n"
        "        return {}\n"
    )
    with pytest.raises(ToolCreationError, match="top-level function"):
        validate_impl_defines_function(class_only, "sample")


def test_validate_impl_accepts_top_level_def_after_imports() -> None:
    code = (
        "import json\n"
        "from typing import Any\n"
        "\n"
        "def sample(q: str) -> dict:\n"
        "    return {}\n"
    )
    validate_impl_defines_function(code, "sample")


def test_validate_impl_rejects_syntax_error() -> None:
    """AST-based validation catches bad Python before we round-trip to the registry."""
    with pytest.raises(ToolCreationError, match="syntax error"):
        validate_impl_defines_function("def sample(:\n    pass\n", "sample")


def test_validate_impl_not_fooled_by_def_inside_a_string() -> None:
    """A matching-name `def` hidden in a docstring must not satisfy validation.

    Regex-based validators would accept this and fail later at import time;
    AST walk only sees the real top-level module body.
    """
    code = (
        '"""Docstring example:\n'
        "\n"
        "    def sample():\n"
        "        ...\n"
        '"""\n'
        "x = 1\n"
    )
    with pytest.raises(ToolCreationError, match="top-level function"):
        validate_impl_defines_function(code, "sample")


def test_validate_impl_rejects_nested_def_only() -> None:
    """A `def sample` only inside a wrapper function shouldn't qualify."""
    code = (
        "def outer():\n"
        "    def sample():\n"
        "        return 1\n"
        "    return sample\n"
    )
    with pytest.raises(ToolCreationError, match="top-level function"):
        validate_impl_defines_function(code, "sample")
