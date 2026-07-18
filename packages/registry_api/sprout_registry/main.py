"""
sprout_registry/main.py
─────────────────────
SproutRegistryAPI — FastAPI REST layer over the Sprout registry.

Exposes the registry as a local HTTP service so that:
  - Agents on any machine / framework can discover and call tools
  - Synthesis pipeline (Docker) can register new tools via multipart upload
  - Tools hot-load into running agents without restart

Endpoints
─────────
  GET  /health                      -> server status + tool count
  GET  /audio                       -> serve a generated audio file
  GET  /tools                       -> list all registered tools
  GET  /tools/{tool_id}             -> single tool spec + JSON schema
  POST /tools/register              -> multipart: spec_file + impl_file
  POST /tools/{tool_id}/execute     -> {"args": {...}} -> result dict
  POST /tools/{tool_id}/test        -> run spec.yaml fixtures, return report
  DELETE /tools/{tool_id}           -> unregister a tool

  POST /synthesis/callback          -> receive synthesis result, auto-register tool

Usage:
    uvicorn sprout_registry.main:app --host 0.0.0.0 --port 8766 --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import yaml
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from slowapi.errors import RateLimitExceeded

from sprout_shared.auth import SproutUser, invalidate_api_key_cache, require_auth, require_jwt_auth, verify_internal_secret
from sprout_shared.config import get_config
from sprout_shared.cors import install_cors
from sprout_shared.env import required_url as _required_url
from sprout_shared.httpx_client import async_client
from sprout_shared.metrics import mount_metrics
from sprout_shared.rate_limit import get_limiter, sprout_rate_limit_exceeded_handler
from sprout_shared.request_id import SproutRequestIDMiddleware
from sprout_shared.spec import SproutTool

from .loader import SproutLoader
from .registry import get_global_registry
from .semantic import get_semantic_index, refresh_semantic_index, rerank_with_embeddings


async def _refresh_index_nonblocking() -> None:
    """Rebuild the semantic index off the event loop.

    ``refresh_semantic_index`` walks every registered tool to re-tokenize
    and rebuild the inverted index. It's O(tokens) — milliseconds today,
    but grows with the registry. Running it inline in an async handler
    would pin the event loop; using ``asyncio.to_thread`` keeps request
    latency flat as the registry scales.
    """
    await asyncio.to_thread(refresh_semantic_index, get_global_registry().list_all())


class ExecuteToolRequest(BaseModel):
    """Body for ``POST /tools/{id}/execute``.

    Validated by FastAPI before the handler runs. Caps the arg + env_var
    sizes so a malicious client can't send a 1 GB JSON and blow up the
    subprocess executor.
    """

    args: dict[str, object] = Field(
        default_factory=dict,
        description="Keyword arguments to pass to the tool function",
    )
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Per-execution env vars (e.g. API keys)",
    )


class FavoriteRequest(BaseModel):
    """Body for ``POST /tools/{id}/favorite``."""

    delta: int = Field(1, description="+1 to favorite, -1 to unfavorite")

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

REGISTRY_DIR         = Path(__file__).parent.parent.parent.parent / "registry" / "tools"

app = FastAPI(
    title="SproutRegistryAPI",
    description=(
        "Local tool registry and execution service. "
        "Agents call /tools to discover tools and /tools/{id}/execute to run them. "
        "The synthesis pipeline registers new tools via POST /tools/register."
    ),
    version="1.0.0",
    lifespan=None,
)

# Per-user rate limiting (slowapi). Default limit comes from
# `SPROUT_RATE_LIMIT_DEFAULT`; the /tools/{id}/execute route below tightens
# further with `@limiter.limit(...)` because tool execution can be expensive.
limiter = get_limiter()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, sprout_rate_limit_exceeded_handler)

# Per-request correlation ID — must come before CORS so it's included on
# every response, including OPTIONS preflight.
app.add_middleware(SproutRequestIDMiddleware)

# CORS: strict allowlist + fail-loud in production if CORS_ORIGINS is unset.
install_cors(app)

mount_metrics(app, "registry_api")

# ── Startup ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    from sprout_shared.logging_config import setup_logging
    setup_logging()

    """Initialize database and load all tools from disk into the registry."""
    import json as _json

    from .db import db_upsert_tool, init_db

    # Initialize async database (creates tables if needed)
    await init_db()
    logger.info("Database initialized")

    # Load all tools from disk into the in-process registry
    if REGISTRY_DIR.exists():
        loader = SproutLoader(auto_register=True)
        tools  = loader.load_all(str(REGISTRY_DIR))
        logger.info(f"Loaded {len(tools)} tools from {REGISTRY_DIR}")

        # Sync tool metadata to async database
        for tool in tools:
            s = tool.spec
            await db_upsert_tool(
                tool_id=s.id,
                name=s.name,
                spec_json=_json.dumps(_tool_to_dict(tool)),
                description=s.description,
                version=s.version,
                author=s.author,
                category=s.category,
                tags_json=_json.dumps(s.tags),
            )
        logger.info(f"Synced {len(tools)} tools to database")
    else:
        logger.info(f"Registry dir not found: {REGISTRY_DIR} — starting empty")

    refresh_semantic_index(get_global_registry().list_all())
    yield


app.router.lifespan_context = _lifespan


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tool_to_dict(tool) -> dict:
    """Convert a SproutTool to a JSON-serialisable summary dict."""
    s = tool.spec
    return {
        "id":          s.id,
        "name":        s.name,
        "version":     s.version,
        "description": s.description,
        "author":      s.author,
        "category":    s.category,
        "tags":        s.tags,
        "required_env_vars": list(getattr(s, "required_env_vars", None) or []),
        "params": [
            {
                "name":        p.name,
                "type":        p.type,
                "description": p.description,
                "required":    p.required,
                "default":     p.default,
                "enum":        p.enum,
            }
            for p in s.params
        ],
    }


def _tool_def(tool) -> dict:
    """
    Build the LLM-ready tool definition (OpenAI / Mistral compatible JSON schema).
    Agents can pass this directly to their LLM tool_choice parameter.
    """
    s = tool.spec
    properties: dict[str, Any] = {}
    required: list[str] = []

    _TYPE_TO_JSON = {
        "str": "string", "int": "integer", "float": "number",
        "bool": "boolean", "list": "array", "dict": "object",
    }

    for p in s.params:
        prop: dict[str, Any] = {
            "type":        _TYPE_TO_JSON.get(p.type, "string"),
            "description": p.description or "",
        }
        if p.enum:
            prop["enum"] = p.enum
        if p.default is not None:
            prop["default"] = p.default
        properties[p.name] = prop
        if p.required:
            required.append(p.name)

    return {
        "type": "function",
        "function": {
            "name":        s.name,
            "description": s.description,
            "parameters": {
                "type":       "object",
                "properties": properties,
                "required":   required,
            },
        },
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/livez", summary="Liveness — process is up and serving")
def livez():
    """Cheap liveness probe. Returns 200 as long as the process is alive.

    Used by orchestrators (k8s, docker-compose) to decide whether to restart
    the container. MUST NOT call out to dependencies — a slow DB should not
    cause us to be killed and restarted.
    """
    return {"status": "ok", "service": "sprout-registry-api"}


@app.get("/readyz", summary="Readiness — dependencies are reachable")
async def readyz():
    """Real readiness probe. Pings the async DB and the in-process registry.

    Returns 200 only when the service can actually serve traffic. Used by
    orchestrators to decide whether to send requests to this instance.
    Returns 503 if any check fails so /tools requests get routed elsewhere.
    """
    from sqlalchemy import text

    from .db import get_session

    checks: dict[str, str] = {}
    overall = "ok"

    # In-process registry: cheap, just confirms tools were loaded at boot.
    try:
        registry = get_global_registry()
        checks["registry"] = f"ok ({len(registry)} tools)"
    except Exception as exc:
        checks["registry"] = f"error: {exc!s}"
        overall = "degraded"

    # Async DB: real round-trip with a tiny query.
    try:
        async with get_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc!s}"
        overall = "degraded"

    body = {
        "status": overall,
        "service": "sprout-registry-api",
        "checks": checks,
    }
    if overall != "ok":
        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/health", summary="Combined health: live + ready (legacy compat)")
async def health():
    """Combined health endpoint kept for backwards compatibility.

    New deployments should use /livez and /readyz separately. This route
    runs the readiness checks AND adds the legacy ``tool_count`` field that
    the iter-20 test_main.py and the Puppeteer e2e tests assert against.
    """
    ready = await readyz()
    if isinstance(ready, JSONResponse):
        # Readiness failed — propagate the 503 but enrich the body so old
        # callers see the legacy fields too.
        body = json.loads(ready.body)
        body["tool_count"] = len(get_global_registry())
        return JSONResponse(status_code=503, content=body)

    return {
        "status": "ok",
        "service": "sprout-registry-api",
        "tool_count": len(get_global_registry()),
        "checks": ready["checks"],
    }


@app.get("/audio", summary="Serve a generated audio file by absolute path")
def serve_audio(path: str):
    """Serve an audio file produced by a tool (e.g. ElevenLabs TTS)."""
    file = Path(path)
    if not file.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    suffix = file.suffix.lower()
    media_types = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".flac": "audio/flac"}
    return FileResponse(file, media_type=media_types.get(suffix, "application/octet-stream"))


@app.get("/tools/stats", summary="Registry statistics")
def tool_stats():
    """Returns tool count, categories breakdown, and tag distribution."""
    registry = get_global_registry()
    tools = registry.list_all()
    categories: dict[str, int] = {}
    tags: dict[str, int] = {}
    authors: set[str] = set()
    for t in tools:
        cat = t.spec.category or "uncategorized"
        categories[cat] = categories.get(cat, 0) + 1
        for tag in t.spec.tags:
            tags[tag] = tags.get(tag, 0) + 1
        if t.spec.author:
            authors.add(t.spec.author)
    return {
        "total": len(tools),
        "categories": dict(sorted(categories.items(), key=lambda x: -x[1])),
        "tags": dict(sorted(tags.items(), key=lambda x: -x[1])[:20]),
        "unique_authors": len(authors),
    }


def _stats_to_dict(stat) -> dict:
    """Serialize a ToolStatModel row (or None) to a stats dict.

    Returns a zero-valued dict if stat is None so the UI can render the same
    shape for tools that have never been executed yet.
    """
    if stat is None:
        return {
            "execution_count": 0,
            "success_count": 0,
            "error_count": 0,
            "success_rate": None,
            "avg_duration_ms": 0.0,
            "last_executed_at": None,
            "last_status": "never",
            "favorite_count": 0,
        }
    total = stat.execution_count
    return {
        "execution_count": stat.execution_count,
        "success_count": stat.success_count,
        "error_count": stat.error_count,
        "success_rate": (stat.success_count / total) if total > 0 else None,
        "avg_duration_ms": round(stat.avg_duration_ms, 2),
        "last_executed_at": stat.last_executed_at.isoformat() if stat.last_executed_at else None,
        "last_status": stat.last_status,
        "favorite_count": stat.favorite_count,
    }


@app.get("/tools", summary="List all registered tools (with execution stats)")
async def list_tools():
    """
    Returns every registered tool with its spec, LLM-ready tool_def, AND
    execution stats (count, success rate, last_executed_at, favorites).

    Agents should call this at the top of every loop iteration to pick up
    tools registered by the synthesis pipeline since the last call. The
    catalog UI uses the same response to render the tool cards with their
    social-proof signals (executions, favorites, last status badge).
    """
    from .db import db_list_all_tool_stats

    registry = get_global_registry()
    stats_by_id = await db_list_all_tool_stats()
    return [
        {
            **_tool_to_dict(t),
            "tool_def": _tool_def(t),
            "stats": _stats_to_dict(stats_by_id.get(t.id)),
        }
        for t in registry.list_all()
    ]


@app.get("/tools/search", summary="Search tools by query string")
async def search_tools(q: str = "", mode: str = "semantic", limit: int = 20):
    """Find tools for a query.

    Modes:
      - ``semantic`` *(default)* — BM25 ranking over name/description/tags/id.
        Agents should prefer this; it tolerates paraphrase ("ycombinator news"
        → ``hackernews_top``) and returns a confidence score suitable for
        gating synthesis.
      - ``lexical`` — legacy SQL LIKE search for backwards compatibility with
        the registry UI's exact-token filter.

    The response is a ranked list; the first item is the best match.
    """
    q = (q or "").strip()
    if not q:
        return []
    # Clamp `limit` so an unauthenticated caller can't force us to
    # serialize arbitrarily large responses (OOM + bandwidth amplification).
    limit = max(1, min(limit, 100))

    if mode == "lexical":
        from .db import db_search_tools

        results = await db_search_tools(q)
        registry = get_global_registry()
        matched = []
        for row in results:
            tool = registry.get(row.id)
            if tool:
                matched.append({**_tool_to_dict(tool), "tool_def": _tool_def(tool)})
        return matched

    hits = get_semantic_index().search(q, limit=limit)
    return [
        {
            **_tool_to_dict(hit.tool),
            "tool_def": _tool_def(hit.tool),
            "score": round(hit.score, 4),
            "confidence": round(hit.confidence, 4),
        }
        for hit in hits
    ]


class RouteIntentRequest(BaseModel):
    """Body for ``POST /tools/route``."""

    intent: str = Field(..., min_length=1, max_length=500, description="Natural-language description of what the agent wants to do")
    min_confidence: float = Field(0.0, ge=0.0, le=1.0, description="Discard hits below this confidence (0..1)")
    rerank: bool = Field(False, description="Rerank the top BM25 hits with mistral-embed (requires MISTRAL_API_KEY)")
    limit: int = Field(5, ge=1, le=20, description="Max number of ranked candidates to return")


@app.post("/tools/route", summary="Route a natural-language intent to the best registered tool")
async def route_intent(body: RouteIntentRequest):
    """Map an intent like "give me hacker news top stories" onto the best
    registered tool. Callers that already know which tool they want should
    keep calling ``/tools/{id}/execute`` directly — this endpoint exists so
    agents, synthesis pre-checks, and UIs can *discover* a match without
    hardcoding tool IDs.

    Response shape::

        {
          "intent": "...",
          "match":    { tool_def, tool_id, confidence, score, args_suggestion },
          "runner_up": { ... } | None,
          "candidates": [ ... up to limit ... ],
          "reranked":  bool,
        }

    `args_suggestion` is a best-guess mapping of the intent's tokens to the
    top tool's required string params — helpful for the UI to pre-fill a
    try-it-now form but NOT a substitute for proper argument extraction by
    the calling LLM.
    """
    hits = get_semantic_index().search(body.intent, limit=body.limit)
    reranked = False
    if body.rerank and hits:
        new_order = await rerank_with_embeddings(body.intent, hits, top_k=min(body.limit, 8))
        reranked = new_order is not hits
        hits = new_order

    if body.min_confidence > 0:
        hits = [h for h in hits if h.confidence >= body.min_confidence]

    candidates = [
        {
            "tool_id": hit.tool.id,
            "name": hit.tool.spec.name,
            "description": hit.tool.spec.description,
            "confidence": round(hit.confidence, 4),
            "score": round(hit.score, 4),
            "tool_def": _tool_def(hit.tool),
        }
        for hit in hits
    ]

    def _args_suggestion(tool: SproutTool | None) -> dict[str, Any]:
        # Pre-fill only when the tool has exactly one required string param
        # without an enum or default. For tools with multiple free-form
        # string inputs (e.g. a translator taking {source_text, target_lang})
        # we can't tell which slot the intent belongs in, so returning {}
        # is safer than dumping the intent into every slot and producing
        # garbage when the caller forwards it to /execute.
        if tool is None:
            return {}
        candidates = [
            p for p in tool.spec.params
            if p.required and p.type == "str" and p.enum is None and p.default is None
        ]
        if len(candidates) == 1:
            return {candidates[0].name: body.intent}
        return {}

    top_tool = hits[0].tool if hits else None
    match = candidates[0] | {"args_suggestion": _args_suggestion(top_tool)} if candidates else None
    runner_up = candidates[1] if len(candidates) > 1 else None

    return {
        "intent": body.intent,
        "match": match,
        "runner_up": runner_up,
        "candidates": candidates,
        "reranked": reranked,
    }


@app.get("/tools/versions/{tool_id:path}", summary="List all versions of a tool")
def list_tool_versions(tool_id: str):
    """Returns all available versions for a tool, sorted newest first."""
    tool_dir = REGISTRY_DIR / tool_id
    if not tool_dir.exists():
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found on disk")

    versions = []
    for ver_dir in sorted(tool_dir.iterdir(), reverse=True):
        if not ver_dir.is_dir():
            continue
        spec_path = ver_dir / "spec.yaml"
        if not spec_path.exists():
            continue
        try:
            raw = yaml.safe_load(spec_path.read_text())
            versions.append({
                "version": ver_dir.name,
                "description": raw.get("tool", {}).get("description", ""),
                "author": raw.get("tool", {}).get("author", ""),
                "generated_by": raw.get("metadata", {}).get("generated_by", ""),
            })
        except Exception:
            versions.append({"version": ver_dir.name, "error": "Could not parse spec"})

    return {"tool_id": tool_id, "versions": versions, "count": len(versions)}


@app.get("/tools/{tool_id:path}/stats", summary="Per-tool execution statistics")
async def get_tool_stats(tool_id: str):
    """Return execution stats for a single tool, or zeros if never run.

    Declared BEFORE the catch-all ``GET /tools/{tool_id:path}`` because the
    `:path` qualifier on that route would otherwise greedily match
    ``com.sprout.tools.foo/stats`` as a tool id and 404.
    """
    from .db import db_get_tool_stats

    stat = await db_get_tool_stats(tool_id)
    if stat is None:
        return {
            "tool_id": tool_id,
            "execution_count": 0,
            "success_count": 0,
            "error_count": 0,
            "success_rate": None,
            "avg_duration_ms": 0.0,
            "last_executed_at": None,
            "last_status": "never",
            "favorite_count": 0,
        }
    total = stat.execution_count
    success_rate = (stat.success_count / total) if total > 0 else None
    return {
        "tool_id": stat.tool_id,
        "execution_count": stat.execution_count,
        "success_count": stat.success_count,
        "error_count": stat.error_count,
        "success_rate": success_rate,
        "avg_duration_ms": round(stat.avg_duration_ms, 2),
        "last_executed_at": stat.last_executed_at.isoformat() if stat.last_executed_at else None,
        "last_status": stat.last_status,
        "favorite_count": stat.favorite_count,
    }


@app.get("/tools/{tool_id:path}/integrations", summary="Copy-pasteable integration snippets for every supported framework")
def get_tool_integrations(tool_id: str, request: Request):
    """Generate cURL / Python / OpenAI / AG2 / LangChain / Pydantic-AI /
    Mistral / MCP snippets for one tool. Snippet generation is purely
    textual and does not import any framework SDK.
    """
    from .integrations import integrations_for

    registry = get_global_registry()
    tool = registry.get(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")

    # Use the public registry URL the request came in on so snippets
    # reference whatever host the user actually hits (localhost in dev,
    # custom domain in prod). Falls back to env-configured value.
    public_url = (
        os.environ.get("SPROUT_PUBLIC_REGISTRY_URL")
        or f"{request.url.scheme}://{request.url.netloc}"
    )
    mcp_url = os.environ.get("SPROUT_PUBLIC_MCP_URL")  # None falls back to heuristic
    return {
        "tool_id": tool.id,
        "name": tool.spec.name,
        "registry_url": public_url,
        "integrations": integrations_for(tool, public_url, mcp_url=mcp_url),
    }


@app.get("/tools/{tool_id:path}", summary="Get a single tool by ID")
def get_tool(tool_id: str):
    """Returns the full spec + LLM tool_def for one tool."""
    registry = get_global_registry()
    tool = registry.get(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    return {**_tool_to_dict(tool), "tool_def": _tool_def(tool)}


@app.post("/tools/register", summary="Register a new tool (multipart upload)")
async def register_tool(
    spec_file: UploadFile = File(..., description="spec.yaml — validated against sprout.schema.json"),
    impl_file: UploadFile = File(..., description="Python implementation file, e.g. weather.py"),
    _user: SproutUser = Depends(require_auth),
):
    """
    Register a new tool from a spec.yaml + implementation .py file.

    Workflow:
      1. Parse spec_file to extract tool.id and tool.version
      2. Save both files to registry/tools/{tool_id}/{version}/
      3. Run test fixtures (if any defined in spec.yaml)
      4. If all fixtures pass, load + register the tool
      5. Return the result — including fixture report

    Called by the synthesis pipeline (Docker) after synthesising a new tool.

    curl example:
        curl -X POST http://localhost:8766/tools/register \\
             -F "spec_file=@spec.yaml" \\
             -F "impl_file=@weather.py"
    """
    # ── Read uploaded bytes ────────────────────────────────────────────────────
    spec_bytes = await spec_file.read()
    impl_bytes = await impl_file.read()

    # ── Parse spec to get id + version for directory placement ────────────────
    try:
        raw = yaml.safe_load(spec_bytes)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid YAML in spec_file: {exc}") from exc

    tool_id = raw.get("tool", {}).get("id")
    version = str(raw.get("tool", {}).get("version", "1.0.0"))

    if not tool_id:
        raise HTTPException(status_code=422, detail="spec.yaml must contain tool.id")

    # ── Safety: check for blocked imports ─────────────────────────────────────
    from .safety import validate_imports
    violations = validate_imports(impl_bytes.decode("utf-8", errors="replace"))
    if violations:
        raise HTTPException(
            status_code=422,
            detail={"message": "Tool uses blocked packages", "violations": violations},
        )

    # ── Write to a temp dir, validate + test, then persist ───────────────────
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path  = Path(tmp)
        spec_path = tmp_path / "spec.yaml"
        impl_name = impl_file.filename or f"{raw.get('tool', {}).get('name', 'tool')}.py"
        impl_path = tmp_path / impl_name

        spec_path.write_bytes(spec_bytes)
        impl_path.write_bytes(impl_bytes)

        loader = SproutLoader(auto_register=False)

        # ── Run fixtures first (don't register if they fail) ─────────────────
        report = loader.test(str(tmp_path))

        if report["failed"] > 0:
            failed_details = [
                r for r in report["results"] if not r["passed"]
            ]
            raise HTTPException(
                status_code=422,
                detail={
                    "message":  "Tool failed test fixtures — not registered",
                    "fixtures": failed_details,
                },
            )

        # ── All fixtures passed -> persist files to registry ──────────────────
        dest_dir = REGISTRY_DIR / tool_id / version
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(spec_path, dest_dir / "spec.yaml")
        shutil.copy(impl_path, dest_dir / impl_name)

        # ── Load + register from the persistent location ─────────────────────
        loader_reg = SproutLoader(auto_register=True)
        tool = loader_reg.load(str(dest_dir))

    # Sync to async database for search
    import json as _json

    from .db import db_upsert_tool
    s = tool.spec
    await db_upsert_tool(
        tool_id=s.id, name=s.name,
        spec_json=_json.dumps(_tool_to_dict(tool)),
        description=s.description, version=s.version,
        author=s.author, category=s.category,
        tags_json=_json.dumps(s.tags),
    )

    await _refresh_index_nonblocking()

    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "tool_id": tool.id,
            "version": tool.spec.version,
            "fixtures": {
                "passed": report["passed"],
                "failed": report["failed"],
            },
        },
    )


TOOL_EXECUTOR_URL = _required_url("TOOL_EXECUTOR_URL", "http://localhost:8767")


def _read_tool_source(tool_id: str) -> tuple[str, str, list[str]] | None:
    """Read a tool's source code, function name, and requirements from disk.

    Returns (code, function_name, requirements) or None if not found.
    """
    tool_dir = None
    for candidate in sorted(REGISTRY_DIR.glob(f"{tool_id}/*/"), reverse=True):
        if (candidate / "spec.yaml").exists():
            tool_dir = candidate
            break

    if tool_dir is None:
        return None

    try:
        spec_raw = yaml.safe_load((tool_dir / "spec.yaml").read_text())
    except Exception:
        return None

    entrypoint = spec_raw.get("implementation", {}).get("entrypoint", "impl.py")
    function_name = spec_raw.get("tool", {}).get("name", "")
    deps = spec_raw.get("implementation", {}).get("dependencies", [])

    if not function_name:
        return None

    impl_path = tool_dir / entrypoint
    if not impl_path.exists():
        return None

    return impl_path.read_text(), function_name, deps


@app.post("/tools/{tool_id:path}/execute", summary="Execute a registered tool")
@limiter.limit(os.environ.get("SPROUT_RATE_LIMIT_EXECUTE", "60/minute"))
async def execute_tool(
    request: Request,
    tool_id: str,
    body: ExecuteToolRequest,
    _user: SproutUser = Depends(require_auth),
):
    """
    Execute a registered tool with the provided arguments.

    Delegates execution to the Tool Executor service for isolated subprocess
    execution. Falls back to in-process execution if the executor is unavailable.
    Records execution stats (count, success rate, duration) regardless of path.

    Returns:
        {"success": true, "tool_id": "...", "result": {...}}
    """
    import time

    from .db import db_record_execution

    registry = get_global_registry()
    tool = registry.get(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")

    args = dict(body.args)
    # Sandbox contract: expose ONLY the env vars the tool declared in its spec.
    # Anything else the caller sent — including vars the caller "happened to
    # know" the user had set — is dropped. Missing declared vars are omitted
    # entirely (not set to "") so os.getenv returns None and os.environ[...]
    # raises KeyError, matching standard Python semantics.
    declared = frozenset(getattr(tool.spec, "required_env_vars", None) or ())
    incoming = dict(body.env_vars)
    env_vars = {k: v for k, v in incoming.items() if k in declared and v}
    dropped = sorted(set(incoming) - set(env_vars))
    if dropped:
        logger.info(
            "execute %s: dropped undeclared/empty env vars %s (declared=%s)",
            tool_id, dropped, sorted(declared),
        )

    started_at = time.perf_counter()
    success = False
    try:
        # Try delegating to the Tool Executor service
        source = _read_tool_source(tool_id)
        if source is not None:
            code, function_name, requirements = source
            config = get_config()
            try:
                # Use the request-id-aware client so the executor's logs
                # get the same correlation ID as the originating call.
                async with async_client(timeout=60) as client:
                    resp = await client.post(
                        f"{TOOL_EXECUTOR_URL}/execute",
                        json={
                            "tool_id": tool_id,
                            "function_name": function_name,
                            "code": code,
                            "args": args,
                            "requirements": requirements,
                            "timeout": 30,
                            "env_vars": env_vars,
                        },
                        headers={"X-Internal-Secret": config.internal_secret},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("success"):
                            success = True
                            return {"success": True, "tool_id": tool_id, "result": data["result"]}
                        else:
                            raise HTTPException(status_code=500, detail=data.get("error", "Executor error"))
            except httpx.ConnectError:
                logger.debug("Tool Executor unavailable, falling back to in-process execution")
            except httpx.TimeoutException:
                logger.warning("Tool Executor timed out for %s", tool_id)

        # Fallback: in-process execution (local dev without executor running).
        # Inject env vars via a threading lock to avoid concurrent requests
        # clobbering each other's os.environ entries.
        import threading
        _env_lock = threading.Lock()
        _env_saved: dict[str, str | None] = {}
        if env_vars:
            with _env_lock:
                for k, v in env_vars.items():
                    _env_saved[k] = os.environ.get(k)
                    os.environ[k] = v
        try:
            try:
                result = tool.fn(**args)
                success = True
            except TypeError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid arguments for '{tool_id}': {exc}",
                ) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Tool '{tool_id}' raised {type(exc).__name__}: {exc}",
                ) from exc
        finally:
            if env_vars:
                with _env_lock:
                    for k, original in _env_saved.items():
                        if original is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = original

        return {"success": True, "tool_id": tool_id, "result": result}
    finally:
        duration_ms = (time.perf_counter() - started_at) * 1000.0
        try:
            await db_record_execution(tool_id, success=success, duration_ms=duration_ms)
        except Exception as exc:
            logger.warning("Failed to record execution stats for %s: %s", tool_id, exc)


@app.post("/tools/{tool_id:path}/favorite", summary="Toggle the favorite count")
async def toggle_favorite(
    tool_id: str,
    body: FavoriteRequest | None = None,
    _user: SproutUser = Depends(require_auth),
):
    """Increment or decrement the favorite count for a tool.

    Body: ``{"delta": 1}`` to favorite, ``{"delta": -1}`` to unfavorite.
    Defaults to +1 if body is omitted entirely.
    """
    from .db import db_toggle_favorite

    delta = body.delta if body is not None else 1
    if delta not in (-1, 1):
        raise HTTPException(status_code=422, detail="delta must be +1 or -1")

    new_count = await db_toggle_favorite(tool_id, delta)
    return {"tool_id": tool_id, "favorite_count": new_count}


@app.post("/tools/{tool_id:path}/test", summary="Run test fixtures for a tool")
def test_tool(tool_id: str):
    """
    Re-run the spec.yaml fixtures for an already-registered tool.
    Useful for regression testing after updating a tool.
    """
    # Find the tool's directory on disk
    tool_dir = None
    for candidate in REGISTRY_DIR.glob(f"{tool_id}/*/"):
        if (candidate / "spec.yaml").exists():
            tool_dir = candidate

    if tool_dir is None:
        raise HTTPException(
            status_code=404,
            detail=f"Tool directory for '{tool_id}' not found on disk",
        )

    loader = SproutLoader(auto_register=False)
    report = loader.test(str(tool_dir))
    return report


@app.delete("/tools/{tool_id:path}", summary="Unregister a tool")
def delete_tool(tool_id: str, _user: SproutUser = Depends(require_auth)):
    """
    Remove a tool from the in-memory registry.
    Does NOT delete files from disk (to allow re-registration).
    """
    registry = get_global_registry()
    if not registry.has(tool_id):
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    registry.unregister(tool_id)
    refresh_semantic_index(registry.list_all())
    return {"success": True, "tool_id": tool_id}


# ── Synthesis Callback Endpoint ───────────────────────────────────────────────

@app.post("/synthesis/callback", summary="Receive synthesis result and auto-register tool")
async def synthesis_callback(
    request: Request,
    tool_id:  str        = Form(...,  description="Sprout tool ID, e.g. com.sprout.tools.weather"),
    spec:     UploadFile = File(...,  description="Generated spec.yaml"),
    impl:     UploadFile = File(...,  description="Generated impl.py"),
    env_vars: str | None = Form(None, description="JSON-encoded list of required env var dicts"),
):
    """
    Called by the synthesis pipeline (Docker) when tool synthesis is complete.

    Accepts multipart/form-data with:
        tool_id  — Sprout tool ID
        spec     — spec.yaml file
        impl     — impl.py file
        env_vars — optional JSON string of [{name, description}, ...]

    On success: validates fixtures, saves files to registry, loads + registers the tool.

    Returns:
        {"success": true,  "tool_id": "...", "version": "...", "fixtures": {...}}
    """
    verify_internal_secret(request)

    spec_bytes = await spec.read()
    impl_bytes = await impl.read()

    # Parse spec to get id, version, and entrypoint filename
    try:
        raw = yaml.safe_load(spec_bytes)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid YAML in spec: {exc}") from exc

    resolved_tool_id = raw.get("tool", {}).get("id") or tool_id
    version          = str(raw.get("tool", {}).get("version", "1.0.0"))
    entrypoint       = raw.get("implementation", {}).get("entrypoint", "impl.py")

    if not resolved_tool_id:
        raise HTTPException(status_code=422, detail="spec.yaml must contain tool.id")

    # Safety: check for blocked imports
    from .safety import validate_imports as _validate_imports
    _violations = _validate_imports(impl_bytes.decode("utf-8", errors="replace"))
    if _violations:
        logger.warning("Synthesis callback rejected for %s: %s", resolved_tool_id, _violations)
        return JSONResponse(
            status_code=422,
            content={"success": False, "message": "Blocked packages detected", "violations": _violations},
        )

    # Validate spec schema before touching disk
    loader_check = SproutLoader(auto_register=False)
    try:
        jsonschema.validate(instance=raw, schema=loader_check._schema)
    except jsonschema.ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"spec.yaml schema invalid: {exc.message}") from exc

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path  = Path(tmp)
        spec_path = tmp_path / "spec.yaml"
        impl_path = tmp_path / entrypoint

        spec_path.write_bytes(spec_bytes)
        impl_path.write_bytes(impl_bytes)

        loader = SproutLoader(auto_register=False)
        report = loader.test(str(tmp_path))

        if report["failed"] > 0:
            failed_details = [r for r in report["results"] if not r["passed"]]
            logger.warning(f"tool {resolved_tool_id} failed fixtures — not registered")
            return JSONResponse(
                status_code=422,
                content={
                    "success":  False,
                    "tool_id":  resolved_tool_id,
                    "message":  "Tool failed test fixtures — not registered",
                    "fixtures": failed_details,
                },
            )

        # All fixtures passed and spec is valid -> persist to registry
        dest_dir = REGISTRY_DIR / resolved_tool_id / version
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(spec_path, dest_dir / "spec.yaml")
        shutil.copy(impl_path, dest_dir / entrypoint)

        loader_reg = SproutLoader(auto_register=True)
        tool = loader_reg.load(str(dest_dir))

    # Sync to async database for search
    import json as _json

    from .db import db_upsert_tool as _db_upsert
    s = tool.spec
    await _db_upsert(
        tool_id=s.id, name=s.name,
        spec_json=_json.dumps(_tool_to_dict(tool)),
        description=s.description, version=s.version,
        author=s.author, category=s.category,
        tags_json=_json.dumps(s.tags),
    )

    refresh_semantic_index(get_global_registry().list_all())

    logger.info(f"registered tool {resolved_tool_id} v{version}")
    return {
        "success":  True,
        "tool_id":  tool.id,
        "version":  tool.spec.version,
        "fixtures": {
            "passed": report["passed"],
            "failed": report["failed"],
        },
    }


# ── Auth / API Key Management Endpoints ──────────────────────────────────────


@app.post("/auth/api-key", summary="Generate an API key for the authenticated user")
async def create_api_key(user: SproutUser = Depends(require_jwt_auth)):
    """
    Generate a new API key for CLI/MCP access. JWT auth required (no API key fallback).
    If a key already exists, returns the existing key.
    """
    import secrets

    config = get_config()
    if not config.clerk_secret_key:
        raise HTTPException(status_code=500, detail="CLERK_SECRET_KEY not configured")

    # Check if user already has a key
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://api.clerk.com/v1/users/{user.user_id}",
            headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
        )
        resp.raise_for_status()
        user_data = resp.json()

    existing_key = user_data.get("private_metadata", {}).get("api_key", "")
    if existing_key:
        return {"api_key": existing_key, "message": "Existing key returned"}

    # Generate new key
    api_key = f"sprout_{user.user_id}_{secrets.token_hex(16)}"

    # Store in Clerk user metadata
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"https://api.clerk.com/v1/users/{user.user_id}/metadata",
            headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
            json={"private_metadata": {"api_key": api_key}},
        )
        resp.raise_for_status()

    return {"api_key": api_key, "message": "API key created"}


@app.get("/auth/api-key", summary="Get the current user's API key (masked)")
async def get_api_key(user: SproutUser = Depends(require_jwt_auth)):
    """Returns the user's API key with all but the last 4 characters masked."""
    config = get_config()
    if not config.clerk_secret_key:
        raise HTTPException(status_code=500, detail="CLERK_SECRET_KEY not configured")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://api.clerk.com/v1/users/{user.user_id}",
            headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
        )
        resp.raise_for_status()
        user_data = resp.json()

    api_key = user_data.get("private_metadata", {}).get("api_key", "")
    if not api_key:
        raise HTTPException(status_code=404, detail="No API key generated yet. Call POST /auth/api-key first.")

    masked = "*" * (len(api_key) - 4) + api_key[-4:]
    return {"api_key": masked}


@app.post("/auth/api-key/regenerate", summary="Regenerate the user's API key")
async def regenerate_api_key(user: SproutUser = Depends(require_jwt_auth)):
    """Invalidates the old key and generates a new one."""
    import secrets

    config = get_config()
    if not config.clerk_secret_key:
        raise HTTPException(status_code=500, detail="CLERK_SECRET_KEY not configured")

    # Get old key to invalidate cache
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://api.clerk.com/v1/users/{user.user_id}",
            headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
        )
        resp.raise_for_status()
        user_data = resp.json()

    old_key = user_data.get("private_metadata", {}).get("api_key", "")
    if old_key:
        invalidate_api_key_cache(old_key)

    # Generate and store new key
    api_key = f"sprout_{user.user_id}_{secrets.token_hex(16)}"

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"https://api.clerk.com/v1/users/{user.user_id}/metadata",
            headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
            json={"private_metadata": {"api_key": api_key}},
        )
        resp.raise_for_status()

    return {"api_key": api_key, "message": "API key regenerated"}


# ── Tool Environment Variables (per-user, stored in Clerk metadata) ─────────


@app.get("/auth/tool-env-vars", summary="Get saved tool env vars (masked)")
async def get_tool_env_vars(user: SproutUser = Depends(require_jwt_auth)):
    """Returns all saved tool environment variables with values masked."""
    config = get_config()
    if not config.clerk_secret_key:
        raise HTTPException(status_code=500, detail="CLERK_SECRET_KEY not configured")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://api.clerk.com/v1/users/{user.user_id}",
            headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
        )
        resp.raise_for_status()
        user_data = resp.json()

    env_vars = user_data.get("private_metadata", {}).get("tool_env_vars", {})
    masked = {}
    for name, value in env_vars.items():
        if len(value) > 4:
            masked[name] = "*" * (len(value) - 4) + value[-4:]
        else:
            masked[name] = "****"
    return {"env_vars": masked}


@app.put("/auth/tool-env-vars", summary="Save tool env vars for the authenticated user")
async def put_tool_env_vars(body: dict, user: SproutUser = Depends(require_jwt_auth)):
    """
    Merge new tool env vars into the user's saved set.
    Body: {"env_vars": {"SERPER_API_KEY": "...", "NEWS_API_KEY": "..."}}
    """
    config = get_config()
    if not config.clerk_secret_key:
        raise HTTPException(status_code=500, detail="CLERK_SECRET_KEY not configured")

    new_vars = body.get("env_vars", {})
    if not new_vars or not isinstance(new_vars, dict):
        raise HTTPException(status_code=422, detail="'env_vars' dict is required")

    # Read existing metadata to merge (Clerk PATCH merges top-level but replaces nested)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://api.clerk.com/v1/users/{user.user_id}",
            headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
        )
        resp.raise_for_status()
        user_data = resp.json()

    existing = user_data.get("private_metadata", {}).get("tool_env_vars", {})
    merged = {**existing, **new_vars}

    # Write back
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"https://api.clerk.com/v1/users/{user.user_id}/metadata",
            headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
            json={"private_metadata": {"tool_env_vars": merged}},
        )
        resp.raise_for_status()

    return {"message": f"Saved {len(new_vars)} env var(s)", "saved": list(new_vars.keys())}


@app.delete("/auth/tool-env-vars/{var_name}", summary="Delete a saved tool env var")
async def delete_tool_env_var(var_name: str, user: SproutUser = Depends(require_jwt_auth)):
    """Remove a single tool env var from the user's saved set."""
    config = get_config()
    if not config.clerk_secret_key:
        raise HTTPException(status_code=500, detail="CLERK_SECRET_KEY not configured")

    # Read existing
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://api.clerk.com/v1/users/{user.user_id}",
            headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
        )
        resp.raise_for_status()
        user_data = resp.json()

    existing = user_data.get("private_metadata", {}).get("tool_env_vars", {})
    if var_name not in existing:
        raise HTTPException(status_code=404, detail=f"Env var '{var_name}' not found")

    del existing[var_name]

    # Write back
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"https://api.clerk.com/v1/users/{user.user_id}/metadata",
            headers={"Authorization": f"Bearer {config.clerk_secret_key}"},
            json={"private_metadata": {"tool_env_vars": existing}},
        )
        resp.raise_for_status()

    return {"message": f"Deleted '{var_name}'"}
