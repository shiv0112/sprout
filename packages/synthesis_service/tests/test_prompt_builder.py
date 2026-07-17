"""Regression tests for ``_build_test_input`` in ``prompt_builder``.

The function builds a sample test invocation dict from a ``SynthesizeRequest``
and serializes it to JSON. Before iter 5, the local ``sample = {}`` was
inferred as ``dict[str, str]`` by mypy, but the function actually assigned
``int``, ``float``, ``bool``, ``list``, and ``dict`` values. Iter 5 fixed
this by annotating ``sample: dict[str, object]``.

These tests pin the runtime contract — every supported Kiln type must
round-trip through ``json.dumps`` cleanly with the right Python type.
"""

from __future__ import annotations

import json

from kiln_synthesis.models import SynthesizeRequest, ToolInput
from kiln_synthesis.prompt_builder import _build_test_input


def _make_request(inputs: list[ToolInput]) -> SynthesizeRequest:
    return SynthesizeRequest(
        tool_name="dummy",
        description="A test fixture tool",
        inputs=inputs,
    )


def test_string_input_produces_test_value() -> None:
    request = _make_request([ToolInput(name="city", type="string")])
    sample = json.loads(_build_test_input(request))
    assert sample == {"city": "test"}


def test_integer_input_produces_int() -> None:
    request = _make_request([ToolInput(name="count", type="integer")])
    sample = json.loads(_build_test_input(request))
    assert sample == {"count": 1}
    assert isinstance(sample["count"], int)


def test_float_input_produces_float() -> None:
    request = _make_request([ToolInput(name="price", type="number")])
    sample = json.loads(_build_test_input(request))
    assert sample == {"price": 1.0}
    assert isinstance(sample["price"], float)


def test_boolean_input_produces_bool() -> None:
    request = _make_request([ToolInput(name="enabled", type="boolean")])
    sample = json.loads(_build_test_input(request))
    assert sample == {"enabled": True}
    assert isinstance(sample["enabled"], bool)


def test_array_input_produces_empty_list() -> None:
    request = _make_request([ToolInput(name="tags", type="array")])
    sample = json.loads(_build_test_input(request))
    assert sample == {"tags": []}
    assert isinstance(sample["tags"], list)


def test_object_input_produces_empty_dict() -> None:
    request = _make_request([ToolInput(name="config", type="object")])
    sample = json.loads(_build_test_input(request))
    assert sample == {"config": {}}
    assert isinstance(sample["config"], dict)


def test_enum_input_produces_first_value() -> None:
    request = _make_request([
        ToolInput(name="size", type="enum", values=["small", "medium", "large"])
    ])
    sample = json.loads(_build_test_input(request))
    assert sample == {"size": "small"}


def test_unknown_type_falls_back_to_string() -> None:
    request = _make_request([ToolInput(name="weird", type="some_made_up_type")])
    sample = json.loads(_build_test_input(request))
    assert sample == {"weird": "test"}


def test_mixed_input_types_round_trip_through_json() -> None:
    """Real regression of the iter-5 fix.

    A single SynthesizeRequest with EVERY supported type. Before iter 5,
    mypy would have rejected this; the runtime crash risk was a
    confusing JSON serialization error if the dict were ever typed
    strictly. Pin that all supported types coexist in one sample.
    """
    request = _make_request([
        ToolInput(name="name", type="string"),
        ToolInput(name="count", type="integer"),
        ToolInput(name="ratio", type="number"),
        ToolInput(name="active", type="boolean"),
        ToolInput(name="tags", type="array"),
        ToolInput(name="meta", type="object"),
    ])
    raw = _build_test_input(request)
    sample = json.loads(raw)

    assert sample == {
        "name": "test",
        "count": 1,
        "ratio": 1.0,
        "active": True,
        "tags": [],
        "meta": {},
    }


def test_optional_input_with_default_is_skipped() -> None:
    """Optional params that have a default are skipped — the tool's own
    default fills them in. Optional params *without* a default still
    need a test value, otherwise the fixture call would be empty.
    """
    request = _make_request([
        ToolInput(name="required_one", type="string"),
        ToolInput(name="optional_with_default", type="string", required=False, default="bar"),
        ToolInput(name="optional_no_default", type="string", required=False),
    ])
    sample = json.loads(_build_test_input(request))
    assert sample == {
        "required_one": "test",
        "optional_no_default": "test",
    }
    assert "optional_with_default" not in sample
