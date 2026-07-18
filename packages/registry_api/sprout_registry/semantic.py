"""
sprout_registry/semantic.py
─────────────────────────
Semantic tool discovery — maps a natural-language *intent*
("get the weather for Tokyo") onto the best-matching registered tool
without the caller needing to know its ID.

Two-stage retrieval so it works everywhere:

    stage 1  BM25 lexical scorer      (pure Python, always available)
    stage 2  Mistral-embed rerank     (optional — enabled when
                                       MISTRAL_API_KEY is set)

The index lives in process memory and is rebuilt on every
register/unregister call. Rebuild is O(n · tokens) — a few
milliseconds for thousands of tools. Search is O(|query_terms| · |posting|),
which is sub-millisecond at the scale we care about.

The public surface is intentionally small:

    idx = SemanticIndex()
    idx.rebuild(registry.list_all())
    hits = idx.search("latest bitcoin price", limit=5)
    route = idx.route("show me hacker news top stories")

`search` and `route` are sync. The optional Mistral-embedding rerank
happens inside `route` only when the caller opts in via `rerank=True`,
so the /tools/search path stays synchronous and dependency-free.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sprout_shared.spec import SproutTool

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z0-9]+")

# Short stopword list. We keep it small on purpose — for a tool registry,
# rare domain words ("satellite", "bitcoin", "lyrics") carry most of the
# signal. Over-aggressive stopword removal would hurt recall on two-word
# queries like "get weather".
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "for", "with", "by", "at", "from", "as",
    "and", "or", "but", "if", "then", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "my", "your", "our",
    "me", "him", "her", "us", "them",
    "please", "can", "could", "would", "should", "will", "give", "get",
    "fetch", "find", "do", "does", "did", "any", "some",
})


def _tokenize(text: str) -> list[str]:
    """Lowercase, alphanumeric-only, stopword-filtered tokens.

    CamelCase / snake_case are broken apart by the alphanumeric split,
    so `get_weather_forecast` → [weather, forecast] after stopwords.
    """
    return [w for w in (m.group().lower() for m in _WORD_RE.finditer(text)) if w not in _STOPWORDS and len(w) > 1]


@dataclass
class SearchHit:
    """One search result with a score."""
    tool: SproutTool
    score: float
    # Confidence is score normalized to [0, 1] using the top hit as
    # denominator. Callers (the /tools/route gate) use this, not the raw
    # BM25 score, because BM25 scores depend on corpus size.
    confidence: float


class SemanticIndex:
    """BM25 index over tool `name + description + tags + id`.

    Safe to call `rebuild` and `search` from different threads — we take
    a write lock on rebuild and a read-under-lock snapshot on search.
    """

    # BM25 tunables. k1 controls term frequency saturation, b controls
    # length normalization. These are the canonical defaults and are
    # fine for the short, uniform-length documents a tool spec produces.
    _K1 = 1.5
    _B = 0.75

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tools: list[SproutTool] = []
        self._doc_tokens: list[list[str]] = []
        self._doc_len: list[int] = []
        self._avg_doc_len: float = 0.0
        self._postings: dict[str, dict[int, int]] = defaultdict(dict)
        self._idf: dict[str, float] = {}

    # ── Indexing ──────────────────────────────────────────────────────────────

    @staticmethod
    def _doc_text(tool: SproutTool) -> str:
        """Concatenate the fields that describe what a tool *does*.

        We include the id and param names as well as tags, because users
        sometimes type fragments of the id ("yt transcript") rather than
        the description wording.
        """
        spec = tool.spec
        parts = [
            tool.id.replace(".", " ").replace("_", " "),
            spec.name.replace("_", " "),
            spec.description,
            " ".join(spec.tags),
            spec.category or "",
            " ".join(p.name.replace("_", " ") for p in spec.params),
        ]
        return " ".join(p for p in parts if p)

    def rebuild(self, tools: Iterable[SproutTool]) -> None:
        """Rebuild the full index from scratch. Cheap for thousands of tools."""
        with self._lock:
            self._tools = list(tools)
            self._doc_tokens = [_tokenize(self._doc_text(t)) for t in self._tools]
            self._doc_len = [len(d) for d in self._doc_tokens]
            self._avg_doc_len = (sum(self._doc_len) / len(self._doc_len)) if self._doc_len else 0.0

            self._postings = defaultdict(dict)
            for doc_id, tokens in enumerate(self._doc_tokens):
                for term, freq in Counter(tokens).items():
                    self._postings[term][doc_id] = freq

            n = len(self._tools)
            # Robertson-Spärck Jones IDF used by BM25: log((N - df + 0.5) / (df + 0.5) + 1).
            # The `+ 1` keeps IDF non-negative for terms appearing in every doc,
            # which would otherwise score as 0 and silently break recall.
            self._idf = {
                term: math.log(((n - len(posting) + 0.5) / (len(posting) + 0.5)) + 1.0)
                for term, posting in self._postings.items()
            }
            logger.info("SemanticIndex rebuilt: %d tools, %d unique terms", n, len(self._postings))

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _bm25(self, query_terms: list[str]) -> dict[int, float]:
        """Compute BM25 scores for every doc that shares at least one query term.

        Docs that share no terms never enter the result set — they'd score
        0 anyway and walking them wastes cycles on a large registry.
        """
        scores: dict[int, float] = defaultdict(float)
        if self._avg_doc_len == 0:
            return scores
        for term in query_terms:
            posting = self._postings.get(term)
            if not posting:
                continue
            idf = self._idf.get(term, 0.0)
            for doc_id, freq in posting.items():
                dl = self._doc_len[doc_id]
                # Canonical BM25 term score. The denominator's length
                # normalization penalizes long descriptions so a tool with
                # one matching keyword in a short description outranks a
                # tool that mentions the keyword once in a wall of text.
                denom = freq + self._K1 * (1 - self._B + self._B * dl / self._avg_doc_len)
                scores[doc_id] += idf * (freq * (self._K1 + 1)) / denom
        return scores

    def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        """Return the top `limit` tools ranked by BM25, best first."""
        with self._lock:
            if not self._tools:
                return []
            query_terms = _tokenize(query)
            if not query_terms:
                return []
            raw = self._bm25(query_terms)
            if not raw:
                return []
            ranked = sorted(raw.items(), key=lambda kv: kv[1], reverse=True)[:limit]
            top_score = ranked[0][1] or 1.0
            return [
                SearchHit(
                    tool=self._tools[doc_id],
                    score=score,
                    confidence=min(1.0, score / top_score),
                )
                for doc_id, score in ranked
            ]

    # ── Intent routing ────────────────────────────────────────────────────────

    def route(self, intent: str, *, min_confidence: float = 0.0) -> list[SearchHit]:
        """Rank tools for an *intent*. Thin wrapper that also filters by score."""
        hits = self.search(intent, limit=5)
        if min_confidence <= 0:
            return hits
        return [h for h in hits if h.confidence >= min_confidence]


# ── Optional Mistral-embedding rerank ────────────────────────────────────────
#
# Kept in the same module, but gated behind `MISTRAL_API_KEY` + explicit
# opt-in from the caller. We rerank only the top-K lexical hits, which
# keeps the embedding cost flat regardless of registry size and prevents
# a misconfigured Mistral client from tanking routing latency.

# Lazily-initialised singleton. A fresh `Mistral()` per call re-opens the
# connection pool (and per-process TLS handshake) on every rerank — fine
# for occasional use but wasteful once an agent starts routing every
# intent. We cache the client and bind it to the API key so a rotated
# env var forces a rebuild.
_mistral_client: tuple[str, Any] | None = None


def _get_mistral_client() -> Any | None:
    """Return a cached Mistral client, or None if the key/SDK is missing."""
    global _mistral_client
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        return None
    if _mistral_client is not None and _mistral_client[0] == api_key:
        return _mistral_client[1]
    try:
        from mistralai import Mistral
    except ImportError:
        return None
    client = Mistral(api_key=api_key)
    _mistral_client = (api_key, client)
    return client


async def rerank_with_embeddings(
    intent: str,
    hits: list[SearchHit],
    *,
    top_k: int = 8,
) -> list[SearchHit]:
    """Return `hits` reordered by cosine similarity to the intent embedding.

    If the Mistral client or API key is missing, returns `hits` unchanged.
    Callers should treat this as a best-effort enhancement, not a hard
    dependency — semantic routing still works with BM25 alone.

    When reranking succeeds we return *only* the top-K (reranked) hits,
    **not** ``rescored + hits[top_k:]``. Mixing cosine-scored heads with
    BM25-scored tails would produce a list whose ``confidence`` fields
    live on two incompatible scales — any downstream ``min_confidence``
    gate would be lying to the caller. If a caller wants more than K
    results, they can re-request with a larger ``top_k``; the cost is
    one extra embedding per doc.
    """
    if not hits or top_k <= 0:
        return hits
    client = _get_mistral_client()
    if client is None:
        return hits

    subset = hits[:top_k]
    docs = [SemanticIndex._doc_text(h.tool) for h in subset]

    try:
        response = await client.embeddings.create_async(
            model="mistral-embed",
            inputs=[intent, *docs],
        )
    except Exception as exc:  # noqa: BLE001 — any Mistral failure degrades gracefully
        logger.warning("Mistral embedding rerank failed (%s); falling back to BM25", exc)
        return hits

    vectors = [d.embedding for d in response.data]
    qv, dvs = vectors[0], vectors[1:]

    # Precompute the query norm once — it's constant across every doc
    # comparison, so recomputing per-doc burns O(N · |qv|) for no reason.
    q_norm = math.sqrt(sum(x * x for x in qv)) or 1.0

    def _cos_score(dv: list[float]) -> float:
        dot = sum(x * y for x, y in zip(qv, dv, strict=True))
        d_norm = math.sqrt(sum(x * x for x in dv)) or 1.0
        return dot / (q_norm * d_norm)

    rescored = [
        SearchHit(tool=h.tool, score=_cos_score(dv), confidence=0.0)
        for h, dv in zip(subset, dvs, strict=True)
    ]
    rescored.sort(key=lambda h: h.score, reverse=True)
    top = rescored[0].score or 1.0
    # Re-normalize confidence against the new top cosine so the gate
    # threshold in /tools/route means the same thing in BM25-only mode
    # and embedding-rerank mode.
    for h in rescored:
        h.confidence = min(1.0, h.score / top)
    return rescored


# ── Global index singleton ───────────────────────────────────────────────────

_global_index: SemanticIndex | None = None


def get_semantic_index() -> SemanticIndex:
    global _global_index
    if _global_index is None:
        _global_index = SemanticIndex()
    return _global_index


def refresh_semantic_index(tools: Iterable[SproutTool]) -> None:
    """Rebuild the global index. Safe to call on hot paths — it's O(ms)."""
    get_semantic_index().rebuild(tools)
