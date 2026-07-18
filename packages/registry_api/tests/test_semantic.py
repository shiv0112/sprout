"""Unit tests for ``sprout_registry.semantic``.

Covers BM25 index correctness on a small fixture corpus. The rerank-with-
embeddings path is deliberately not tested here — it's a network-dependent
best-effort enhancement whose contract ("returns input unchanged on any
failure") is stronger than anything we'd assert in isolation.
"""

from __future__ import annotations

import pytest

from sprout_registry.semantic import SemanticIndex, _tokenize
from sprout_shared.spec import SproutTool, SproutToolSpec, ToolParam


def _make_tool(
    tool_id: str,
    *,
    name: str,
    description: str,
    tags: list[str] | None = None,
    params: list[ToolParam] | None = None,
) -> SproutTool:
    return SproutTool(
        spec=SproutToolSpec(
            id=tool_id,
            name=name,
            description=description,
            tags=tags or [],
            params=params or [],
        ),
        fn=lambda **_kw: None,
    )


@pytest.fixture
def sample_corpus() -> list[SproutTool]:
    """A small set of tools with overlapping and disjoint vocabulary."""
    return [
        _make_tool(
            "com.sprout.tools.weather_forecast",
            name="weather_forecast",
            description="Fetch multi-day weather forecast for any location using Open-Meteo.",
            tags=["weather", "meteorology", "no-api-key"],
            params=[ToolParam("city", "str", "City name", required=True)],
        ),
        _make_tool(
            "com.sprout.tools.hackernews_top",
            name="hackernews_top",
            description="Retrieve the current top stories from Hacker News by Y Combinator.",
            tags=["hackernews", "ycombinator", "news", "tech"],
        ),
        _make_tool(
            "com.sprout.tools.wikipedia_search",
            name="wikipedia_search",
            description="Search Wikipedia and return article summaries.",
            tags=["wikipedia", "encyclopedia"],
        ),
        _make_tool(
            "com.sprout.tools.stock_quote",
            name="stock_quote",
            description="Get the latest stock price for a ticker symbol.",
            tags=["finance", "stocks"],
        ),
    ]


def test_tokenize_strips_stopwords_and_short_tokens() -> None:
    assert _tokenize("get the weather for Tokyo") == ["weather", "tokyo"]
    assert _tokenize("A B C") == [], "single-char tokens should be dropped"


def test_tokenize_splits_snake_and_camel() -> None:
    # Alphanumeric-only split handles snake_case and CamelCase uniformly.
    tokens = _tokenize("HackerNewsTop get_weather_forecast")
    assert "hackernewstop" in tokens  # camel not split at case boundary — by design, kept cheap
    assert "weather" in tokens
    assert "forecast" in tokens


def test_search_returns_best_match_for_paraphrased_intent(sample_corpus: list[SproutTool]) -> None:
    """Paraphrase test: "ycombinator news" should rank hackernews_top first.

    This is the core semantic discovery claim — the registry must find the
    tool even when the query doesn't contain its literal name. BM25 gets
    there because "ycombinator" is rare (high IDF) and appears in exactly
    one tool's description + tags.
    """
    idx = SemanticIndex()
    idx.rebuild(sample_corpus)

    hits = idx.search("ycombinator news", limit=3)
    assert hits, "expected at least one hit"
    assert hits[0].tool.id == "com.sprout.tools.hackernews_top"
    assert hits[0].confidence == 1.0, "top hit is always normalized to confidence 1.0"


def test_search_handles_exact_tool_name(sample_corpus: list[SproutTool]) -> None:
    idx = SemanticIndex()
    idx.rebuild(sample_corpus)

    hits = idx.search("weather forecast", limit=1)
    assert hits and hits[0].tool.id == "com.sprout.tools.weather_forecast"


def test_search_empty_query_returns_empty(sample_corpus: list[SproutTool]) -> None:
    idx = SemanticIndex()
    idx.rebuild(sample_corpus)

    assert idx.search("") == []
    assert idx.search("   ") == []
    # Query that tokenizes to nothing (all stopwords) should also return empty.
    assert idx.search("the of is and a") == []


def test_search_no_match_returns_empty(sample_corpus: list[SproutTool]) -> None:
    idx = SemanticIndex()
    idx.rebuild(sample_corpus)

    # "genealogy" appears in no tool — lexical BM25 should return nothing
    # rather than hallucinating a weak match.
    assert idx.search("genealogy research assistant") == []


def test_rebuild_is_idempotent(sample_corpus: list[SproutTool]) -> None:
    """Calling rebuild twice produces identical results. Guards against
    accumulated state in the inverted index across rebuilds, which would
    silently drift scoring over the lifetime of a long-running process.
    """
    idx = SemanticIndex()
    idx.rebuild(sample_corpus)
    first = [(h.tool.id, round(h.score, 6)) for h in idx.search("hacker news", limit=3)]

    idx.rebuild(sample_corpus)
    second = [(h.tool.id, round(h.score, 6)) for h in idx.search("hacker news", limit=3)]

    assert first == second


def test_rebuild_after_unregister_removes_tool(sample_corpus: list[SproutTool]) -> None:
    idx = SemanticIndex()
    idx.rebuild(sample_corpus)

    assert any(h.tool.id == "com.sprout.tools.stock_quote" for h in idx.search("stock price ticker"))

    shrunk = [t for t in sample_corpus if t.id != "com.sprout.tools.stock_quote"]
    idx.rebuild(shrunk)
    assert not any(h.tool.id == "com.sprout.tools.stock_quote" for h in idx.search("stock price ticker"))


def test_route_filters_by_min_confidence(sample_corpus: list[SproutTool]) -> None:
    idx = SemanticIndex()
    idx.rebuild(sample_corpus)

    # With a 0.5 cutoff we should still get the top hit (confidence 1.0).
    top = idx.route("weather forecast for Tokyo", min_confidence=0.5)
    assert top, "strong query should survive the cutoff"
    assert top[0].tool.id == "com.sprout.tools.weather_forecast"

    # A cutoff of 1.1 is impossible to satisfy — nothing should come back.
    assert idx.route("weather forecast", min_confidence=1.1) == []


def test_search_on_empty_index_returns_empty() -> None:
    idx = SemanticIndex()
    assert idx.search("anything at all") == []
