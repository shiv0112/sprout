"""
sprout_chat_backend/main.py
─────────────────────────
SproutChatBackend — FastAPI app for Sprout chat/execution endpoints.

Exposes the Sprout planner and graph-flow executor as HTTP endpoints:
  POST /sprout/start                → plan graph, detect missing env vars
  POST /sprout/execute/{run_id}     → start execution after supplying env vars
  GET  /sprout/stream/{run_id}      → SSE stream of Sprout execution events
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from queue import Empty, Queue
from typing import Any

import httpx
import requests
import yaml
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from slowapi.errors import RateLimitExceeded

from sprout_shared.auth import SproutUser, require_auth
from sprout_shared.cors import install_cors
from sprout_shared.env import required_url as _required_url
from sprout_shared.httpx_client import async_client
from sprout_shared.metrics import mount_metrics
from sprout_shared.rate_limit import get_limiter, sprout_rate_limit_exceeded_handler
from sprout_shared.request_id import SproutRequestIDMiddleware

from .graph_flow import SproutGraphFlow
from .llm_providers import ag2_config_list, provider_chain
from .planner import SproutPlanner

logger = logging.getLogger(__name__)

load_dotenv()

# ── Constants ─────────────────────────────────────────────────────────────────

REGISTRY_DIR          = Path(__file__).parent.parent.parent.parent / "registry" / "tools"
REGISTRY_URL          = _required_url("REGISTRY_URL", "http://localhost:8766")
SYNTHESIS_URL         = _required_url("SYNTHESIS_URL", "http://localhost:8002")
SPROUT_CALLBACK_URL     = _required_url("SPROUT_CALLBACK_URL", "http://host.docker.internal:8766/synthesis/callback")

app = FastAPI(
    title="SproutChatBackend",
    description=(
        "Sprout chat backend service. "
        "Provides planning, execution and streaming endpoints for the Sprout multi-agent workflow."
    ),
    version="1.0.0",
    lifespan=None,
)

# Per-user rate limiting (slowapi). Default 1000/minute per user/IP via
# `SPROUT_RATE_LIMIT_DEFAULT`; specific routes can tighten further with
# `@limiter.limit(...)` decorators.
limiter = get_limiter()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, sprout_rate_limit_exceeded_handler)

# Per-request correlation ID — must come before CORS so it's set on
# every response including OPTIONS preflight and SSE streams.
app.add_middleware(SproutRequestIDMiddleware)

# CORS: strict allowlist + fail-loud in production if CORS_ORIGINS is unset.
install_cors(app)

mount_metrics(app, "chat_backend")

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    from sprout_shared.logging_config import setup_logging

    from .leader_lock import leader_lock_context
    setup_logging()
    async with leader_lock_context():
        yield


app.router.lifespan_context = _lifespan


@app.get("/livez", summary="Liveness — process is up")
def livez():
    """Cheap liveness probe. Returns 200 as long as the process is alive.

    Used by orchestrators to decide whether to RESTART the container — must
    NOT call out to dependencies, otherwise a slow registry would cause us
    to be killed and restarted.
    """
    return {"status": "ok", "service": "sprout-chat-backend"}


@app.get("/readyz", summary="Readiness — downstream dependencies reachable")
async def readyz():
    """Real readiness probe. Pings the registry_api (and synthesis if set).

    Returns 200 only when the chat backend can actually serve a run. The
    chat backend cannot plan a graph without the registry's tool list, so
    REGISTRY_URL being unreachable is a hard failure that returns 503.
    The synthesis URL is checked but treated as optional — synthesis is
    only needed when the planner declares missing tools.
    """
    checks: dict[str, str] = {}
    overall = "ok"

    # Registry: hard dependency. Without /tools the planner has nothing to
    # work with and every run will fail. Use the request-id-aware client so
    # the readyz hop is correlated with the original request.
    try:
        async with async_client(timeout=2.0) as client:
            resp = await client.get(f"{REGISTRY_URL}/livez")
            if resp.status_code == 200:
                checks["registry_api"] = "ok"
            else:
                checks["registry_api"] = f"unhealthy: HTTP {resp.status_code}"
                overall = "degraded"
    except Exception as exc:
        checks["registry_api"] = f"unreachable: {exc!s}"
        overall = "degraded"

    # Synthesis: soft dependency. Note status but don't fail readyz on it.
    try:
        async with async_client(timeout=2.0) as client:
            resp = await client.get(f"{SYNTHESIS_URL}/health")
            if resp.status_code == 200:
                checks["synthesis_service"] = "ok"
            else:
                checks["synthesis_service"] = f"degraded: HTTP {resp.status_code} (synthesis is optional)"
    except Exception as exc:
        checks["synthesis_service"] = f"unreachable: {exc!s} (synthesis is optional)"

    body = {
        "status": overall,
        "service": "sprout-chat-backend",
        "active_runs": len(_run_queues),
        "checks": checks,
    }
    if overall != "ok":
        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/health", summary="Combined health: live + ready (legacy compat)")
async def health():
    """Combined health endpoint kept for backwards compatibility.

    New deployments should use /livez and /readyz. This route runs the
    readiness checks AND keeps the legacy ``active_runs`` field that
    existing UI code asserts against.
    """
    ready = await readyz()
    if isinstance(ready, JSONResponse):
        # Propagate the 503 — body already contains active_runs.
        return ready
    return ready


# ── Trivial-message fast path ─────────────────────────────────────────────────
#
# Routing every chat through Mistral planning + AG2 execution is wasteful and
# noisy for messages like "hi" or "thanks" — the user sees a multi-step
# "Plan: GreetingAgent → greeting starting → greeting done" preamble for what
# should be a one-line response. Detect those messages here, return a canned
# answer immediately, skip the planner and the multi-agent flow entirely.

_TRIVIAL_RESPONSES: dict[str, str] = {
    "hi": "Hi! I'm Sprout. Ask me anything — I can plan multi-step tasks across the registered tools, and synthesise new ones if I'm missing something.",
    "hello": "Hello! I'm Sprout. What would you like to do?",
    "hey": "Hey! What can I help you with?",
    "yo": "Hey! What's up?",
    "sup": "Not much. What do you want to build?",
    "howdy": "Howdy! What can I do for you?",
    "thanks": "You're welcome! Anything else?",
    "thank you": "You're welcome! Anything else?",
    "ty": "You're welcome!",
    "ok": "Got it. Anything else?",
    "okay": "Got it. Anything else?",
    "k": "Got it.",
    "got it": "👍",
    "cool": "Anything else I can help with?",
    "nice": "Glad it helped. Anything else?",
    "bye": "See you later!",
    "goodbye": "See you later!",
    "cya": "Later!",
    "test": "Got the test message. Sprout chat backend is up — try asking me to do something real, like 'what is the current date' or 'convert 100 USD to EUR'.",
    "ping": "pong",
}

_TRIVIAL_PUNCT = re.compile(r"[!\.\?,\s]+$")


def _trivial_response(user_request: str) -> str | None:
    """Return a canned response if the input is a known trivial message.

    Matches case-insensitively after stripping trailing punctuation/whitespace.
    Only fires for short inputs (≤30 chars) so a longer message that happens
    to start with "hi" still goes through the real planner.
    """
    text = user_request.strip()
    if not text or len(text) > 30:
        return None
    normalised = _TRIVIAL_PUNCT.sub("", text.lower())
    return _TRIVIAL_RESPONSES.get(normalised)


# ── Sprout run state (thread-safe via lock) ─────────────────────────────────────
_run_lock = threading.Lock()
# run_id → Queue of event dicts; None sentinel = stream finished
_run_queues: dict[str, Queue] = {}
# run_id → planned task graph (stored between /sprout/start and /sprout/execute)
_run_plans:  dict[str, dict]  = {}
# run_id → tool IDs being synthesised (so execution thread can wait for them)
_run_awaited_tools: dict[str, list[str]] = {}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _tool_to_dict(tool) -> dict:
    """Convert a tool object to a JSON-serialisable summary dict."""
    s = tool.spec
    return {
        "id":          s.id,
        "name":        s.name,
        "version":     s.version,
        "description": s.description,
        "author":      s.author,
        "category":    s.category,
        "tags":        s.tags,
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


async def _fetch_user_tool_env_vars(user_id: str) -> dict[str, str]:
    """Fetch the user's saved tool env vars from Clerk private_metadata."""
    clerk_secret = os.environ.get("CLERK_SECRET_KEY", "").strip()
    if not clerk_secret:
        return {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.clerk.com/v1/users/{user_id}",
                headers={"Authorization": f"Bearer {clerk_secret}"},
            )
            resp.raise_for_status()
            user_data = resp.json()
        return user_data.get("private_metadata", {}).get("tool_env_vars", {})
    except Exception:
        logger.warning("Failed to fetch user tool env vars for %s", user_id)
        return {}


def _collect_missing_envs(graph: dict, provided: dict[str, str]) -> list[dict]:
    """
    For each tool referenced in the task graph, check whether its
    REQUIRED_ENV_VARS are present in os.environ or in the provided dict.
    Returns a deduplicated list of missing var descriptors.
    """
    seen:   set[str]   = set()
    missing: list[dict] = []

    for node in graph.get("nodes", []):
        for tool_id in node.get("tools", []):
            tool_dir = REGISTRY_DIR / tool_id
            if not tool_dir.exists():
                logger.warning("_collect_missing_envs: tool dir not found: %s", tool_dir)
                continue
            # Pick the latest version directory
            impl_file = None
            for ver_dir in sorted(tool_dir.iterdir()):
                spec_path = ver_dir / "spec.yaml"
                if not spec_path.exists():
                    continue
                raw = yaml.safe_load(spec_path.read_text())
                entrypoint = raw.get("implementation", {}).get("entrypoint", "")
                candidate = ver_dir / entrypoint
                if candidate.exists():
                    impl_file = candidate
                    break

            if impl_file is None:
                logger.warning("_collect_missing_envs: no impl file found for %s", tool_id)
                continue

            # Dynamically load the module to read REQUIRED_ENV_VARS
            try:
                spec = importlib.util.spec_from_file_location("_tmp", impl_file)
                if spec is None or spec.loader is None:
                    logger.warning("_collect_missing_envs: importlib could not create spec for %s", impl_file)
                    continue
                mod  = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                required = getattr(mod, "REQUIRED_ENV_VARS", [])
            except Exception as exc:
                logger.warning("_collect_missing_envs: failed to load %s: %s", impl_file, exc)
                continue

            for ev in required:
                name = ev.get("name", "")
                if not name or name in seen:
                    continue
                seen.add(name)
                saved_value = (os.environ.get(name, "") or provided.get(name, "")).strip()
                if len(saved_value) >= 4:
                    continue
                entry: dict[str, Any] = {
                    "tool_id":     tool_id,
                    "var_name":    name,
                    "description": ev.get("description", ""),
                }
                if ev.get("signup_url"):
                    entry["signup_url"] = ev["signup_url"]
                missing.append(entry)

    logger.info("_collect_missing_envs: checked %d tools, found %d missing env vars", len(seen), len(missing))
    return missing


def _research_api(tool_description: str, api_key: str = "") -> str:
    """
    Ask the primary LLM (Groq, with NVIDIA NIM / Mistral fallback) to recommend
    the best free/open API for a given tool description. Returns a short
    constraints string injected into the synthesis request. Non-critical:
    walks the provider chain and returns "" if every provider fails.
    """
    from openai import OpenAI

    messages = [
        {
            "role": "system",
            "content": (
                "You are an API research assistant. "
                "Given a tool description, recommend the single best free/open HTTP API "
                "that requires NO API key. Reply in 3-5 lines only:\n"
                "1. API name and base URL\n"
                "2. Exact endpoint and query parameters to use\n"
                "3. Response format (JSON/XML) and the key fields to extract\n"
                "4. Any required HTTP headers (e.g. User-Agent)\n"
                "If no completely free option exists, name the cheapest option and its "
                "required env var name. Be concrete and brief — no prose."
            ),
        },
        {"role": "user", "content": f"Tool to implement: {tool_description}"},
    ]

    try:
        # Small helper task → fast non-thinking model.
        providers = provider_chain(mistral_api_key=api_key, reasoning=False)
    except Exception as exc:
        logger.error(f"Could not research API (no LLM provider configured): {exc}")
        return ""

    for p in providers:
        try:
            client = OpenAI(api_key=p["api_key"], base_url=p["base_url"], timeout=60.0)
            resp = client.chat.completions.create(
                model=p["model"], messages=messages, max_tokens=400,
            )
            content = resp.choices[0].message.content
            return (content or "").strip()
        except Exception as exc:
            logger.warning("API research via %s failed (%s); trying next provider", p["name"], exc)
            continue
    return ""


def _route_intent_via_registry(intent: str, min_confidence: float) -> dict | None:
    """Ask the registry's semantic router which tool matches the intent.

    Returns the candidate dict ({tool_id, name, description, confidence, ...})
    only if the registry responds AND the top hit clears `min_confidence`.
    Any HTTP failure (registry down, timeout, 5xx) returns None so the caller
    falls back to the lexical Jaccard heuristic — the router is a strict
    upgrade, not a hard dependency.
    """
    try:
        resp = requests.post(
            f"{REGISTRY_URL}/tools/route",
            json={"intent": intent, "min_confidence": min_confidence, "limit": 3},
            timeout=4,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.debug("semantic router unreachable, falling back to Jaccard: %s", exc)
        return None
    body = resp.json()
    match = body.get("match")
    if not match or float(match.get("confidence", 0.0)) < min_confidence:
        return None
    return match


def _jaccard_similar_tool(missing_spec: dict, existing_tools: list[dict], threshold: float) -> dict | None:
    """Word-level Jaccard between the missing spec and each existing tool.

    Local fallback used when the semantic router is unavailable. Same return
    shape as the network path so callers don't branch.
    """
    missing_desc = missing_spec.get("description", "").lower()
    missing_name = missing_spec.get("id", "").split(".")[-1].replace("_", " ").lower()
    missing_words = set((missing_desc + " " + missing_name).split()) - {"the", "a", "an", "of", "to", "and", "in", "for", "is", "it", "by", "on", "with"}

    if not missing_words:
        return None

    best_match: dict | None = None
    best_score = 0.0

    for tool in existing_tools:
        tool_desc = tool.get("description", "").lower()
        tool_name = tool.get("name", "").lower().replace("_", " ")
        tool_id = tool.get("id", "").split(".")[-1].replace("_", " ").lower()
        tool_words = set((tool_desc + " " + tool_name + " " + tool_id).split()) - {"the", "a", "an", "of", "to", "and", "in", "for", "is", "it", "by", "on", "with"}

        if not tool_words:
            continue

        intersection = missing_words & tool_words
        union = missing_words | tool_words
        score = len(intersection) / len(union) if union else 0.0

        if score > best_score:
            best_score = score
            best_match = tool

    if best_score >= threshold and best_match is not None:
        logger.info(
            "Jaccard similar tool: %s matches missing '%s' (score=%.2f)",
            best_match.get("id"), missing_spec.get("id"), best_score,
        )
        return best_match

    return None


# Confidence floor for accepting a router match in lieu of synthesis.
# Set conservatively: 0.82 gives a strong signal without false-positives
# on overloaded keywords like "data" or "search". Tuned against the
# eight sample intents in tests/test_semantic.py — every intended hit
# clears 0.85, every adversarial query stays well below 0.7.
_ROUTER_CONFIDENCE_GATE = 0.82


def _find_similar_tool(missing_spec: dict, existing_tools: list[dict], threshold: float = 0.3) -> dict | None:
    """Resolve a missing tool spec to an existing registered tool, if any.

    Two-stage:
      1. Hit the registry's semantic router. BM25 over name + description +
         tags, optionally Mistral-rerank. Accept only when confidence
         ≥ _ROUTER_CONFIDENCE_GATE.
      2. If the router is unreachable (or finds nothing strong), fall back
         to the local Jaccard heuristic against the in-memory tools list.

    Returns a dict shaped like the entries in `existing_tools`
    (id/name/description) so the remap step in `_filter_missing_tools`
    works unchanged.
    """
    intent = missing_spec.get("description") or missing_spec.get("id", "")
    if intent:
        match = _route_intent_via_registry(intent, _ROUTER_CONFIDENCE_GATE)
        if match:
            tool_id = match.get("tool_id")
            candidate = next((t for t in existing_tools if t.get("id") == tool_id), None) or {
                "id": tool_id,
                "name": match.get("name", ""),
                "description": match.get("description", ""),
            }
            # The router's confidence is normalized to its own top hit, so it can
            # report ~1.00 for an unrelated tool (e.g. iss_location ->
            # weather_forecast). Confirm the pick with a lexical-overlap check
            # before skipping synthesis — when in doubt, synthesize.
            if _jaccard_similar_tool(missing_spec, [candidate], threshold):
                logger.info(
                    "Router match %s for missing '%s' confirmed by lexical overlap",
                    tool_id, missing_spec.get("id"),
                )
                return candidate
            logger.info(
                "Router match %s for missing '%s' rejected (weak overlap) — will synthesize",
                tool_id, missing_spec.get("id"),
            )

    return _jaccard_similar_tool(missing_spec, existing_tools, threshold)


def _reconcile_orphan_tools(graph: dict, existing_tools: list[dict]) -> None:
    """Promote orphan node tool references into ``missing_tools``.

    The planner sometimes references an invented tool ID in a node's ``tools``
    list but forgets to declare it under ``missing_tools``. Without this, that
    tool is never synthesized and the agent just refuses at execution time
    ("I don't have a tool …"). Here we scan every node's tool references and,
    for any ID that is neither registered nor already declared missing, we
    append a minimal missing-tool spec so synthesis actually fires.
    """
    registered: set[str] = set()
    for t in existing_tools:
        if t.get("id"):
            registered.add(t["id"])
        if t.get("name"):
            registered.add(t["name"])

    missing = graph.get("missing_tools", []) or []
    declared = {m.get("id") for m in missing if isinstance(m, dict) and m.get("id")}

    seen: dict[str, str] = {}
    for node in graph.get("nodes", []):
        for tid in list(node.get("tools", []) or []):
            if not tid or tid in registered or tid in declared or tid in seen:
                continue
            seen[tid] = node.get("task") or node.get("role") or f"Tool {tid}"

    for tid, desc in seen.items():
        norm_id = tid if "." in tid else f"com.sprout.tools.{tid}"
        missing.append({
            "id": norm_id,
            "description": desc,
            "inputs": [],
            "output": {"type": "dict", "fields": []},
        })
        if norm_id != tid:
            for node in graph.get("nodes", []):
                node["tools"] = [norm_id if x == tid else x for x in (node.get("tools") or [])]
        logger.info("Reconciled orphan tool reference '%s' -> queued for synthesis as %s", tid, norm_id)

    graph["missing_tools"] = missing


def _filter_missing_tools(graph: dict, existing_tools: list[dict]) -> tuple[list, dict[str, str]]:
    """Filter out missing tools that already have similar existing tools.

    Returns (truly_missing, remap) where remap maps missing tool IDs to
    existing tool IDs so the graph can be updated.
    """
    missing = graph.get("missing_tools", [])
    if not missing:
        return [], {}

    truly_missing: list = []
    remap: dict[str, str] = {}

    for spec in missing:
        if not isinstance(spec, dict):
            continue
        spec_id = spec.get("id")
        if not spec_id:
            continue
        match = _find_similar_tool(spec, existing_tools)
        if match:
            remap[spec_id] = match["id"]
            logger.info("Skipping synthesis for %s — remapping to existing tool %s", spec_id, match.get("id"))
        else:
            truly_missing.append(spec)

    if remap:
        for node in graph.get("nodes", []):
            node["tools"] = [remap.get(tid, tid) for tid in node.get("tools", [])]
        graph["missing_tools"] = truly_missing

    return truly_missing, remap


def _synthesize_missing_tools(missing_tools: list, api_key: str = "") -> list[dict]:
    """
    Fire-and-forget POST requests to the synthesis service for each missing tool spec.

    Each entry in missing_tools is expected to be a dict produced by SproutPlanner:
        {"id": "com.sprout.tools.foo", "description": "...", "inputs": [...], "output": {...}}

    String entries (legacy planner output) are skipped — only structured specs
    contain enough information for the synthesis service to build a tool.

    Returns list of {"job_id": ..., "tool_id": ..., "status": ...} for each
    synthesis request that was accepted, or [] if the synthesis service is not running.
    """
    if not missing_tools:
        return []

    jobs: list[dict] = []

    def _fire(spec: dict) -> None:
        tool_id     = spec.get("id", "")
        tool_name   = tool_id.split(".")[-1] if tool_id else "unknown_tool"
        description = spec.get("description", "")
        job_id      = str(uuid.uuid4())

        # Ask Mistral to research the best free API for this tool before sending to synthesis
        api_hint = _research_api(description, api_key) if api_key else ""
        if api_hint:
            logger.info(f"{tool_id}: {api_hint[:120]}...")

        payload   = {
            "job_id":       job_id,
            "tool_name":    tool_name,
            "description":  description,
            "inputs":       spec.get("inputs", []),
            "output":       spec.get("output", {}),
            "constraints":  api_hint,
            "callback_url": SPROUT_CALLBACK_URL,
        }
        try:
            resp = requests.post(
                f"{SYNTHESIS_URL}/synthesize",
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            jobs.append({"job_id": job_id, "tool_id": tool_id, "status": "queued"})
            logger.info(f"Synthesis queued for {tool_id}  job={job_id}")
        except Exception as exc:
            logger.error(f"Could not queue synthesis for {tool_id}: {exc}")

    threads = []
    for entry in missing_tools:
        if not isinstance(entry, dict):
            continue           # skip bare string IDs — not enough info
        t = threading.Thread(target=_fire, args=(entry,), daemon=True)
        t.start()
        threads.append(t)

    # Wait briefly so jobs list is populated before we return
    for t in threads:
        t.join(timeout=12)

    return jobs


# ── Request/response schemas ──────────────────────────────────────────────────

class SproutStartRequest(BaseModel):
    """Body for ``POST /sprout/start``.

    Validated by FastAPI before the handler runs. Anything malformed gets
    a 422 with a precise field-level error message — much friendlier than
    the previous ``body.get("request", "")`` defaults that swallowed bad
    input and produced confusing downstream failures.
    """
    request: str = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="Natural-language task description",
    )
    history: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Last N conversation messages for context",
    )
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Per-run env vars (e.g. API keys for synthesized tools)",
    )


class SproutExecuteRequest(BaseModel):
    """Body for ``POST /sprout/execute/{run_id}``."""
    env_vars: dict[str, str] = Field(default_factory=dict)


# ── Sprout Endpoints ────────────────────────────────────────────────────────────

@app.post("/sprout/start", summary="Plan a Sprout run; returns plan + any missing env vars")
@limiter.limit(os.environ.get("SPROUT_RATE_LIMIT_SPROUT_START", "30/minute"))
async def sprout_start(
    request: Request,
    body: SproutStartRequest,
    _user: SproutUser = Depends(require_auth),
):
    """
    Phase 1 of a two-phase start: plan the task graph and check for missing
    environment variables (API keys) required by the planned tools.

    Returns one of:
        {"status": "needs_config", "run_id": "...", "plan": {...}, "missing_envs": [...]}
        {"status": "started",      "run_id": "..."}

    If "needs_config", collect the missing keys and call POST /sprout/execute/{run_id}.
    If "started", connect to GET /sprout/stream/{run_id} immediately.
    """
    # Mistral is now only a fallback (Groq is primary). Pass its key into the
    # chain if present, but require just ONE provider to be configured.
    api_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    try:
        provider_chain(mistral_api_key=api_key)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="No LLM provider configured (set GROQ_API_KEY, NVIDIA_API_KEY, or MISTRAL_API_KEY).",
        ) from None

    user_request = body.request.strip()
    if not user_request:
        # min_length=1 catches empty strings, but a string of pure whitespace
        # would slip through — bounce that here.
        raise HTTPException(status_code=422, detail="'request' must not be only whitespace")

    # Trivial-message fast path: skip the planner and the multi-agent flow.
    # Pre-canned response is pushed straight onto the run queue so the SSE
    # consumer drains it like any other run, but with zero LLM calls and zero
    # planner ceremony in the UI.
    canned = _trivial_response(user_request)
    if canned is not None:
        run_id = str(uuid.uuid4())
        synthetic_graph = {
            "task": user_request,
            "nodes": [
                {
                    "id": "direct",
                    "role": "Sprout",
                    "task": user_request,
                    "tools": [],
                }
            ],
            "edges": [],
            "entry_nodes": ["direct"],
            "exit_node": "direct",
            "missing_tools": [],
        }
        q: Queue = Queue()
        with _run_lock:
            _run_plans[run_id] = synthetic_graph
            _run_queues[run_id] = q
            _run_awaited_tools[run_id] = []
        q.put({"type": "plan_ready", **synthetic_graph})
        q.put({"type": "node_start", "node_id": "direct"})
        q.put({"type": "node_complete", "node_id": "direct", "result": canned})
        q.put({"type": "flow_complete", "final_answer": canned})
        return {"status": "started", "run_id": run_id}

    # Build conversation context from history (last 10 messages)
    history = body.history
    if history:
        context = "\n".join(history[-10:])
        full_request = f"Conversation so far:\n{context}\n\nCurrent request: {user_request}"
    else:
        full_request = user_request

    provided_env: dict[str, str] = dict(body.env_vars)

    # Fetch user's saved tool env vars from Clerk and merge
    saved_env = await _fetch_user_tool_env_vars(_user.user_id)
    provided_env = {**saved_env, **provided_env}  # explicit overrides saved

    # Build tool list by fetching from the registry API
    try:
        tools_resp = requests.get(f"{REGISTRY_URL}/tools", timeout=5)
        tools_resp.raise_for_status()
        tools_list = tools_resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not fetch tools from Sprout registry at {REGISTRY_URL}: {exc}",
        ) from exc

    planner = SproutPlanner(registry_url=REGISTRY_URL, api_key=api_key)
    try:
        graph = planner.plan(full_request, tools=tools_list)
    except Exception as exc:
        err_str = str(exc)
        if "429" in err_str or "rate" in err_str.lower() or "capacity" in err_str.lower():
            raise HTTPException(
                status_code=429,
                detail="LLM rate limit exceeded. Please wait a moment and try again.",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"Planner failed: {err_str[:200]}",
        ) from exc

    run_id = str(uuid.uuid4())
    _run_plans[run_id]  = graph
    _run_queues[run_id] = Queue()

    missing = _collect_missing_envs(graph, provided_env)

    # Catch tools the planner referenced in a node but forgot to declare missing.
    _reconcile_orphan_tools(graph, tools_list)

    # Check if any "missing" tools overlap with existing registered tools
    _filter_missing_tools(graph, tools_list)

    # Trigger synthesis only for truly missing tools
    synthesis_jobs = _synthesize_missing_tools(graph.get("missing_tools", []), api_key=api_key)
    awaited_tool_ids = [j["tool_id"] for j in synthesis_jobs]
    _run_awaited_tools[run_id] = awaited_tool_ids

    if missing:
        resp: dict = {
            "status":       "needs_config",
            "run_id":       run_id,
            "plan":         graph,
            "missing_envs": missing,
        }
        if synthesis_jobs:
            resp["synthesis_jobs"] = synthesis_jobs
        return resp

    # All env vars present — kick off execution (will wait for synthesis if needed)
    _launch_execution(run_id, graph, provided_env, api_key)
    resp = {"status": "started", "run_id": run_id}
    if synthesis_jobs:
        resp["synthesis_jobs"] = synthesis_jobs
    return resp


@app.post("/sprout/execute/{run_id}", summary="Start execution after supplying missing env vars")
async def sprout_execute(
    run_id: str,
    body: SproutExecuteRequest,
    _user: SproutUser = Depends(require_auth),
):
    """
    Phase 2 of a two-phase start: supply the missing environment variables
    and begin executing the already-planned task graph.

    Body:
        {"env_vars": {"SERPER_API_KEY": "...", "NEWS_API_KEY": "..."}}

    Returns:
        {"status": "started", "run_id": "..."}

    Connect to GET /sprout/stream/{run_id} for execution events.
    """
    graph = _run_plans.get(run_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found — call /sprout/start first")

    api_key      = os.environ.get("MISTRAL_API_KEY", "").strip()
    provided_env = dict(body.env_vars)

    _launch_execution(run_id, graph, provided_env, api_key)
    return {"status": "started", "run_id": run_id}


def _launch_execution(run_id: str, graph: dict, extra_env: dict[str, str], api_key: str) -> None:
    """Spawn the background thread that runs SproutGraphFlow and feeds the SSE queue."""
    import time

    q = _run_queues.get(run_id)
    if q is None:
        raise ValueError(f"Run '{run_id}' has no event queue — was /sprout/start called first?")
    awaited_tools = _run_awaited_tools.pop(run_id, [])

    def _run() -> None:
        try:
            q.put({"type": "plan_ready", **graph})

            # ── Wait for synthesis service to finish building any new tools ──
            active_graph = graph  # may be replaced after synthesis completes
            if awaited_tools:
                remaining = set(awaited_tools)
                deadline  = time.time() + 120   # 2-minute timeout
                synthesis_failures: dict[str, str] = {}

                q.put({"type": "synthesis_wait", "tool_ids": list(remaining)})

                while remaining and time.time() < deadline:
                    # Check registry API to see if the tools have been registered
                    try:
                        resp = requests.get(f"{REGISTRY_URL}/tools", timeout=5)
                        resp.raise_for_status()
                        available_ids = {t["id"] for t in resp.json()}
                        for tid in list(remaining):
                            if tid in available_ids:
                                remaining.discard(tid)
                                q.put({"type": "tool_ready", "tool_id": tid})
                    except Exception:
                        pass

                    for tid in list(remaining):
                        try:
                            status_resp = requests.get(f"{SYNTHESIS_URL}/synthesize/status/{tid}", timeout=3)
                            if not status_resp.ok:
                                continue
                            info = status_resp.json()
                            if info.get("status") == "failed":
                                synthesis_failures[tid] = str(info.get("error") or "Synthesis failed")
                                remaining.discard(tid)
                        except Exception:
                            pass

                    if synthesis_failures:
                        details = "\n".join(
                            f"  - {tid}: {msg[:240]}"
                            for tid, msg in synthesis_failures.items()
                        )
                        q.put({
                            "type": "flow_complete",
                            "final_answer": (
                                "I couldn't complete this request because tool synthesis failed:\n"
                                f"{details}\n\n"
                                "Please check the synthesis service credentials/configuration and try again."
                            ),
                        })
                        return

                    if remaining:
                        time.sleep(2)

                if remaining:
                    q.put({"type": "synthesis_timeout", "missing": list(remaining)})
                    missing_names = ", ".join(remaining)

                    synth_status = ""
                    for tid in remaining:
                        try:
                            status_resp = requests.get(f"{SYNTHESIS_URL}/synthesize/status/{tid}", timeout=3)
                            if status_resp.ok:
                                info = status_resp.json()
                                synth_status += f"\n  - {tid}: {info.get('status', 'unknown')} — {info.get('error', '')[:200]}"
                        except Exception:
                            synth_status += f"\n  - {tid}: status unknown (synthesis service unreachable)"

                    q.put({
                        "type": "flow_complete",
                        "final_answer": (
                            f"I couldn't complete this request because the required tool(s) "
                            f"({missing_names}) could not be synthesized in time. "
                            f"This usually means the tool generation took longer than 2 minutes "
                            f"or encountered an error."
                            f"{synth_status if synth_status else ''}\n\n"
                            f"You can try again — sometimes it succeeds on retry. "
                            f"If the tool requires an API key, make sure it's configured."
                        ),
                    })
                    return

                # All synthesized tools are now available — re-plan so the graph
                # references the newly registered tool IDs.
                try:
                    tools_resp = requests.get(f"{REGISTRY_URL}/tools", timeout=5)
                    tools_resp.raise_for_status()
                    tools_list = tools_resp.json()
                except Exception:
                    tools_list = []

                planner = SproutPlanner(registry_url=REGISTRY_URL, api_key=api_key)
                active_graph = planner.plan(graph["task"], tools=tools_list)
                q.put({"type": "plan_updated", **active_graph})

            # NVIDIA NIM primary, Mistral fallback. AG2 tries each config_list
            # entry in order and falls back to the next on failure/rate-limit.
            llm_config = {
                "config_list": ag2_config_list(api_key),
                "cache_seed": None,
            }
            flow = SproutGraphFlow(
                registry_url=REGISTRY_URL,
                llm_config=llm_config,
                on_event=q.put,
            )
            final_answer = flow.run(active_graph, extra_env=extra_env, verbose=False)
            q.put({"type": "flow_complete", "final_answer": final_answer})

        except Exception as exc:
            q.put({"type": "error", "message": str(exc)})
        finally:
            q.put(None)  # sentinel — stream is done
            with _run_lock:
                _run_plans.pop(run_id, None)

    threading.Thread(target=_run, daemon=True).start()


@app.get("/sprout/stream/{run_id}", summary="SSE stream of Sprout execution events")
async def sprout_stream(run_id: str):
    """
    Server-Sent Events stream for a Sprout run started via POST /sprout/start.

    Event types:
        plan_ready    — task graph is ready (nodes, edges, order, exit_node)
        node_start    — a node has started executing
        tool_call     — a tool is being called (node_id, tool, args)
        tool_result   — tool returned a result (node_id, tool, result)
        node_complete — a node finished (node_id, result)
        flow_complete — all nodes done (final_answer)
        error         — something went wrong (message)

    Connect with:
        const src = new EventSource('/sprout/stream/<run_id>')
        src.onmessage = (e) => handleEvent(JSON.parse(e.data))
    """
    with _run_lock:
        q = _run_queues.get(run_id)
    if q is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    async def _generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                # Run blocking q.get in thread pool so the event loop stays free
                event = await loop.run_in_executor(None, lambda: q.get(timeout=180))
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except Empty:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Timed out'})}\n\n"
        finally:
            with _run_lock:
                _run_queues.pop(run_id, None)
                _run_plans.pop(run_id, None)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
