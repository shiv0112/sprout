"""HTTP smoke tests for ``kiln_registry.main`` (the FastAPI app).

Uses ``fastapi.testclient.TestClient`` so we exercise the real route
handlers without spinning up uvicorn. The startup event hits the database
and the on-disk registry, so we redirect both to a tmp_path-scoped
location BEFORE importing the app to keep the test hermetic.
"""

from __future__ import annotations

import importlib
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """A TestClient with the registry's startup hook hitting an in-tmp DB.

    Re-imports the module so the module-level ``_global_registry`` and
    ``app`` are constructed against the patched env. Cleans up after.
    """
    db_path = tmp_path / "kiln_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    # Force the legacy sync SQLite to a tmp file too (used by SQLiteRegistry).
    monkeypatch.setenv("KILN_REGISTRY_DB", str(tmp_path / "kiln_registry_test.db"))

    # Reload modules so the env vars take effect on construction.
    import kiln_registry.db as db_module
    import kiln_registry.main as main_module
    import kiln_registry.registry as registry_module
    import kiln_registry.sqlite_registry as sqlite_registry_module

    importlib.reload(sqlite_registry_module)
    importlib.reload(registry_module)
    importlib.reload(db_module)
    importlib.reload(main_module)

    with TestClient(main_module.app) as c:
        yield c


def test_health_returns_ok(client: TestClient) -> None:
    """GET /health returns the expected payload shape."""
    resp = client.get("/health")
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "kiln-registry-api"
    assert isinstance(body["tool_count"], int)
    assert body["tool_count"] >= 0


def test_tools_endpoint_lists_loaded_tools(client: TestClient) -> None:
    """GET /tools returns at least the tools loaded from registry/tools/.

    The startup hook calls ``loader.load_all(REGISTRY_DIR)`` against the
    real on-disk fixtures, so the response should contain current_date
    along with the other 40+ tools.
    """
    resp = client.get("/tools")
    assert resp.status_code == 200

    tools = resp.json()
    assert isinstance(tools, list)
    assert len(tools) > 0, "registry/tools/ exists but loaded 0 tools"

    tool_ids = {t["id"] for t in tools if "id" in t}
    assert "com.kiln.tools.current_date" in tool_ids, (
        f"current_date missing from /tools response. Got: {sorted(tool_ids)[:5]}..."
    )


def test_livez_is_cheap_and_always_returns_200(client: TestClient) -> None:
    """GET /livez returns 200 with no dependency checks.

    Liveness probes are called frequently by orchestrators (every few seconds)
    so they MUST be cheap. Pin: returns 200, no DB query, no registry walk.
    """
    resp = client.get("/livez")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "kiln-registry-api"
    # Liveness should NOT include dependency checks — that's readyz's job.
    assert "checks" not in body


def test_readyz_pings_database_and_registry(client: TestClient) -> None:
    """GET /readyz exercises real downstream dependencies.

    Returns 200 with a per-check status map when everything is reachable.
    Used by orchestrators to decide whether to route traffic.
    """
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "kiln-registry-api"
    assert "checks" in body
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["registry"].startswith("ok")


def test_tools_list_includes_stats_field(client: TestClient) -> None:
    """Iter-40 contract: GET /tools includes a `stats` block per tool.

    Tools that have never been executed get a zero-valued stats dict so the
    catalog UI can render the same shape for everything.
    """
    resp = client.get("/tools")
    assert resp.status_code == 200

    tools = resp.json()
    assert len(tools) > 0

    sample = tools[0]
    assert "stats" in sample, "Every tool in /tools must include a stats block"
    stats = sample["stats"]
    expected_keys = {
        "execution_count",
        "success_count",
        "error_count",
        "success_rate",
        "avg_duration_ms",
        "last_executed_at",
        "last_status",
        "favorite_count",
    }
    assert expected_keys.issubset(stats.keys()), (
        f"stats block missing keys: {expected_keys - stats.keys()}"
    )
    assert stats["last_status"] in {"never", "success", "error"}


def test_tools_search_semantic_mode_ranks_by_intent(client: TestClient) -> None:
    """GET /tools/search?mode=semantic ranks by paraphrased intent.

    Guards the core discovery claim end-to-end: the HTTP layer wires the
    BM25 index, normalizes confidence, and returns the right tool for a
    query that doesn't contain its name.
    """
    resp = client.get("/tools/search", params={"q": "ycombinator news", "mode": "semantic"})
    assert resp.status_code == 200
    hits = resp.json()
    assert hits, "semantic search must find something for a paraphrase we indexed"
    assert hits[0]["id"] == "com.kiln.tools.hackernews_top"
    assert hits[0]["confidence"] == 1.0
    # score and confidence must be present so clients can gate on them.
    assert "score" in hits[0]
    assert "confidence" in hits[0]


def test_tools_search_empty_query_returns_empty_list(client: TestClient) -> None:
    resp = client.get("/tools/search", params={"q": "", "mode": "semantic"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_tools_route_returns_best_match_and_args_suggestion(client: TestClient) -> None:
    """POST /tools/route is the advertised public contract for agents."""
    resp = client.post("/tools/route", json={"intent": "latest bitcoin price"})
    assert resp.status_code == 200
    body = resp.json()

    # Top-level shape.
    assert body["intent"] == "latest bitcoin price"
    assert body["reranked"] is False, "rerank defaults off; no Mistral call"
    assert isinstance(body["candidates"], list)
    assert body["match"] is not None

    match = body["match"]
    # The match must include a tool_def so an agent can call /execute
    # without a second round-trip to fetch the schema.
    assert "tool_def" in match
    assert match["tool_def"]["type"] == "function"
    assert 0.0 <= match["confidence"] <= 1.0
    # args_suggestion is present on the match (may be empty dict).
    assert "args_suggestion" in match


def test_tools_route_rejects_missing_intent(client: TestClient) -> None:
    resp = client.post("/tools/route", json={})
    assert resp.status_code == 422  # FastAPI validation error


def test_tools_route_empty_intent_rejected(client: TestClient) -> None:
    """An empty-string intent must be rejected by pydantic validation rather
    than quietly returning the most-popular tool. Silent ranking on an empty
    query would be worse than a 422 — the caller almost certainly has a bug.
    """
    resp = client.post("/tools/route", json={"intent": ""})
    assert resp.status_code == 422


def test_tool_stats_endpoint_returns_zeros_for_unused_tool(
    client: TestClient,
) -> None:
    """GET /tools/{id}/stats returns zero counters for a tool never executed."""
    resp = client.get("/tools/com.kiln.tools.current_date/stats")
    assert resp.status_code == 200

    body = resp.json()
    assert body["tool_id"] == "com.kiln.tools.current_date"
    # Fresh test DB → counts are zero, last_status is "never".
    assert body["execution_count"] == 0
    assert body["success_count"] == 0
    assert body["error_count"] == 0
    assert body["success_rate"] is None
    assert body["last_status"] == "never"
    assert body["favorite_count"] == 0

