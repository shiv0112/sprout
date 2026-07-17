"""Regression tests for ``KilnPlanner._call_planner`` content-coercion.

The Mistral SDK's ``response.choices[0].message.content`` can be:
- a plain ``str``
- a ``list`` of content chunks (each with a ``.text`` attribute)
- ``None``
- ``Unset`` (when omitted)

The planner must extract a JSON-parseable string from all four shapes.
A regression here would silently break tool planning — pin every shape
so a future "simplification" can't reintroduce the str-only assumption.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kiln_chat_backend.planner import KilnPlanner

# A minimal valid task-graph JSON the planner is "supposed" to return.
SAMPLE_GRAPH = {
    "task": "test request",
    "nodes": [
        {"id": "n1", "role": "GeneralAgent", "task": "do the thing", "tools": []}
    ],
    "edges": [],
    "entry_nodes": ["n1"],
    "exit_node": "n1",
}
SAMPLE_GRAPH_JSON = json.dumps(SAMPLE_GRAPH)


def _build_response(content: object) -> SimpleNamespace:
    """Mimic the shape of ``Mistral().chat.complete()`` return value."""
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


@pytest.fixture
def planner(monkeypatch: pytest.MonkeyPatch) -> KilnPlanner:
    """A KilnPlanner with no real Mistral client.

    The constructor builds a real ``Mistral(api_key=...)`` so we replace
    its ``chat.complete`` method on the instance with a stub via
    ``setattr`` once the test sets ``_next_response``.
    """
    p = KilnPlanner(api_key="dummy-key-not-used")
    return p


def _stub_complete(planner: KilnPlanner, content: object) -> None:
    """Replace the primary provider's ``chat.completions.create`` (OpenAI SDK
    shape) to return ``content``."""
    response = _build_response(content)
    planner._client.chat.completions.create = lambda **kwargs: response  # type: ignore[assignment,method-assign]


def test_str_content_parses(planner: KilnPlanner) -> None:
    """The classic case: content is a JSON string."""
    _stub_complete(planner, SAMPLE_GRAPH_JSON)
    graph = planner._call_planner("test request", tools=[])
    assert graph == SAMPLE_GRAPH


def test_list_of_chunks_content_parses(planner: KilnPlanner) -> None:
    """Content is split across multiple TextChunk-like objects.

    This is the shape mentioned by the Mistral SDK type hints — multimodal
    or streaming responses can return chunked content.
    """
    chunk1 = SimpleNamespace(text='{"task": "test request", "nodes": [')
    chunk2 = SimpleNamespace(
        text='{"id": "n1", "role": "GeneralAgent", "task": "do the thing", "tools": []}'
    )
    chunk3 = SimpleNamespace(
        text='], "edges": [], "entry_nodes": ["n1"], "exit_node": "n1"}'
    )
    _stub_complete(planner, [chunk1, chunk2, chunk3])

    graph = planner._call_planner("test request", tools=[])
    assert graph == SAMPLE_GRAPH


def test_none_content_falls_back_to_single_node(planner: KilnPlanner) -> None:
    """``content is None`` must not raise — the planner must produce a fallback graph.

    This is the bug shape that was masked before the iter-12 fix:
    ``json.loads(None)`` would have crashed with TypeError.
    """
    _stub_complete(planner, None)
    graph = planner._call_planner("test request", tools=[])

    assert graph["task"] == "test request"
    assert isinstance(graph["nodes"], list)
    assert len(graph["nodes"]) >= 1
    # The fallback graph uses node id "fallback" — pin that contract.
    assert graph["nodes"][0]["id"] == "fallback"


def test_list_with_non_text_elements_skips_them(planner: KilnPlanner) -> None:
    """Chunks without a ``.text`` attribute (e.g. images) are silently ignored."""
    image_chunk = SimpleNamespace(image_url="https://example.com/x.png")
    text_chunk = SimpleNamespace(text=SAMPLE_GRAPH_JSON)
    _stub_complete(planner, [image_chunk, text_chunk])

    graph = planner._call_planner("test request", tools=[])
    assert graph == SAMPLE_GRAPH


def test_invalid_json_falls_back_to_single_node(planner: KilnPlanner) -> None:
    """If Mistral returns broken JSON, fall back gracefully.

    Pin the iter-12 behavior: a JSONDecodeError yields a single-node fallback
    graph rather than propagating the exception.
    """
    _stub_complete(planner, "this is not json {{{")
    graph = planner._call_planner("test request", tools=[])

    assert graph["nodes"][0]["id"] == "fallback"
