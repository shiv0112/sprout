"""
sprout_chat_backend/planner.py
────────────────────────────
SproutPlanner — Phase 2.

Converts a natural-language user request into a task graph JSON
by asking Mistral Large to reason over the currently available
Sprout tools on the Sprout registry.

Output schema
─────────────
{
  "task": "original user request",
  "nodes": [
    {
      "id":             "snake_case node id",
      "role":           "Human-readable agent role name",
      "task":           "What this agent should do",
      "tools":          ["com.sprout.tools.weather", ...]   // only tool IDs that exist
    }
  ],
  "edges":        [["from_node_id", "to_node_id"], ...],
  "entry_nodes":  ["node_ids with no incoming edges"],
  "exit_node":    "node_id that synthesises the final answer",
  "missing_tools": [
    {
      "id":          "com.sprout.tools.tool_id",
      "description": "What this tool should do",
      "inputs":      [{"name": "arg", "type": "str", "description": "...", "required": true}],
      "output":      {"type": "dict", "fields": [{"name": "result", "type": "str", "description": "..."}]}
    }
  ]
}

The planner does NOT trigger synthesis — it only reports gaps.
Triggering synthesis is the caller's responsibility.
"""

from __future__ import annotations

import json
import logging

import requests
from openai import OpenAI

from .llm_providers import provider_chain

logger = logging.getLogger(__name__)

PLANNER_SYSTEM = """\
You are the planner for **Sprout**. Sprout is THIS application — a self-hosted, \
self-evolving tool registry for AI agents, built by the team running this \
chat backend. Users talk to you to get real-world tasks done by agents that \
call Sprout-registered Python tools (e.g. fetching weather, converting currency, \
parsing PDFs). You are NOT a documentation chatbot for some external "Sprout" \
product; do not invent URLs like "docs.sprout.tech".

Your job is to convert the user's request into a JSON task graph. There are \
THREE shapes the graph can take, and choosing the right one is the most \
important decision you make:

================================================================================
SHAPE A — META / SELF-REFERENTIAL QUESTIONS  (no tools, no synthesis)
================================================================================
If the user is asking ABOUT Sprout itself — "what is Sprout", "how do I publish a \
tool", "what tools exist", "how does the planner work", "what can you do", \
"who built this" — produce a SINGLE-NODE graph with NO tools and NO \
missing_tools. The agent will answer from its own knowledge of Sprout (which \
you provide in the task field — see the Sprout context below).

Example for "How do I publish a new tool?":
{
  "task": "How do I publish a new tool?",
  "nodes": [
    {
      "id": "answer",
      "role": "SproutExpertAgent",
      "task": "Explain how to publish a new tool to the Sprout registry. Tools live under registry/tools/<tool_id>/<version>/ as a spec.yaml plus an impl.py. The SproutLoader reads spec.yaml at boot, validates against the JSON schema, dynamically imports the function, and registers it. New tools can also be auto-synthesized by the synthesis service when the planner declares a missing_tool. Hot-reload via importlib means no restart needed. The user can publish manually by dropping files in registry/tools/, or via the Publish UI which posts to /tools/register.",
      "tools": []
    }
  ],
  "edges": [],
  "entry_nodes": ["answer"],
  "exit_node": "answer",
  "missing_tools": []
}

NEVER synthesize tools for meta-questions. NEVER call paper_extractor, \
web_search, or fetch_url for questions about Sprout itself. \
If the user wants Sprout docs, you ARE the docs.

================================================================================
SHAPE B — REAL TASK USING EXISTING TOOLS  (use registered tools, no synthesis)
================================================================================
If the user wants something done that maps to one or more REGISTERED tools \
in the list below, build a multi-node graph using those tool IDs only. \
2-7 nodes typical. Pick tools whose name and description ACTUALLY match the \
sub-task — do not pick paper_extractor to read web pages. For reading \
any web page (Wikipedia, news, docs), use fetch_url.

================================================================================
SHAPE C — REAL TASK NEEDING A NEW TOOL  (synthesize sparingly)
================================================================================
ONLY when the user wants a real-world action AND no registered tool covers it, \
declare a missing_tool. The missing tool must:
  - Have a SPECIFIC, ATOMIC purpose (e.g. "fetch BTC price from CoinGecko"), \
    NOT a generic verb like "extract" or "validate".
  - Use a real, free, public HTTP API — never fabricate "docs.sprout.tech" or \
    other made-up endpoints.
  - Be something the synthesis service can actually implement in ~50 lines \
    of Python with `requests`.

DO NOT synthesize tools that:
  - Look up information about Sprout itself (use SHAPE A instead).
  - Do generic "documentation lookup" or "guide extraction" — these always \
    end up hallucinating and call irrelevant tools.
  - Wrap a single web_search call (just use web_search directly if it exists).

================================================================================
JSON SCHEMA  (output raw JSON only, no markdown fences, no commentary)
================================================================================
{
  "task": "<original user request>",
  "nodes": [
    {
      "id":    "<snake_case unique id>",
      "role":  "<agent role>",
      "task":  "<specific sub-task this agent must complete>",
      "tools": ["<tool_id from the registered list>", ...]
    }
  ],
  "edges":         [["<from_id>", "<to_id>"], ...],
  "entry_nodes":   ["<node ids with no incoming edges>"],
  "exit_node":     "<node id of the final synthesis/summary agent>",
  "missing_tools": [
    {
      "id":          "com.sprout.tools.<snake_case_name>",
      "description": "<one specific sentence: what this tool does and which API it calls>",
      "inputs":      [{"name": "<param>", "type": "string|integer|float|boolean", "description": "<desc>", "required": true}],
      "output":      {"type": "dict", "fields": [{"name": "<field>", "type": "string|integer|float|boolean", "description": "<desc>"}]}
    }
  ]
}

Hard rules:
- Tool IDs in any node's "tools" list MUST come from the registered list. Never invent IDs.
- The exit_node synthesises results and uses no tools.
- entry_nodes = all nodes with no incoming edges.
- Prefer the shortest correct plan. If one tool can directly answer in the
  requested currency, unit, or format, use that tool directly instead of
  fetching an intermediate value and converting it in another node.
- Example: for "what is the price of bitcoin in INR?", use `crypto_price`
  with `currency=inr` directly. Do NOT fetch USD first and then call
  `currency_convert` unless the direct tool truly cannot produce INR.
- Output raw JSON only, no markdown fences, no prose around it.
"""


def _extract_text(response: object) -> str:
    """Coerce an OpenAI-style chat response into a JSON-parseable string.

    ``response.choices[0].message.content`` is normally a ``str``, but may be
    ``None`` or a list of content chunks depending on the model/SDK. Also
    strips ```json ... ``` fences some models add despite instructions.
    """
    content = response.choices[0].message.content  # type: ignore[attr-defined]
    if content is None:
        raw = ""
    elif isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        parts: list[str] = []
        for chunk in content:
            text = getattr(chunk, "text", None)
            if text is None and isinstance(chunk, dict):
                text = chunk.get("text")
            if isinstance(text, str):
                parts.append(text)
        raw = "".join(parts)
    else:
        raw = str(content)

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw[3:]
        if raw[:4].lower() == "json":
            raw = raw[4:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    return raw


class SproutPlanner:
    """
    Phase 2: produces a task graph from a user request + Sprout registry tool list.

    Usage:
        planner = SproutPlanner(
            registry_url="http://localhost:8766",
            api_key="...",
        )
        task_graph = planner.plan("Get the weather in Singapore and convert 100 SGD to EUR")
    """

    def __init__(
        self,
        registry_url: str = "http://localhost:8766",
        api_key: str = "",
        model: str | None = None,
    ):
        self._server_url = registry_url.rstrip("/")
        # NVIDIA NIM primary, Mistral fallback (both OpenAI-compatible).
        self._providers  = provider_chain(mistral_api_key=api_key)
        self._clients    = [
            OpenAI(api_key=p["api_key"], base_url=p["base_url"], timeout=120.0)
            for p in self._providers
        ]
        # Primary client/model kept as attributes for back-compat and tests.
        self._client     = self._clients[0]
        self._model      = model or self._providers[0]["model"]

    # ── Public API ─────────────────────────────────────────────────────────────

    def plan(self, user_request: str, tools: list[dict] | None = None) -> dict:
        """
        Produce a task graph for the given user request.

        Args:
            user_request: Natural-language task description.
            tools:        Optional pre-fetched tool list. When provided, the
                          HTTP call to the Sprout registry is skipped. Useful when
                          calling from within the Sprout registry itself to avoid a
                          self-request deadlock.

        Returns:
            Task graph dict (see module docstring for schema).

        Raises:
            requests.ConnectionError  – Sprout registry is not running (only when tools=None)
            json.JSONDecodeError      – Mistral returned non-JSON
        """
        if tools is None:
            tools = self._fetch_tools()
        graph = self._call_planner(user_request, tools)
        return graph

    # ── Private ────────────────────────────────────────────────────────────────

    def _fetch_tools(self) -> list[dict]:
        """GET /tools from the Sprout registry."""
        try:
            resp = requests.get(f"{self._server_url}/tools", timeout=5)
            resp.raise_for_status()
            return resp.json()
        except requests.ConnectionError:
            raise requests.ConnectionError(
                f"Sprout registry not reachable at {self._server_url}. "
                "Run: python run_server.py"
            ) from None

    def _call_planner(self, user_request: str, tools: list[dict]) -> dict:
        tool_summary = "\n".join(
            f"  - {t['id']} [{t.get('category', 'general')}] (tags: {', '.join(t.get('tags', []))}): {t['description'][:200]}"
            for t in tools
        )

        user_msg = (
            f"User request: {user_request}\n\n"
            f"Available Sprout tools:\n{tool_summary}\n\n"
            "Produce the task graph JSON now."
        )

        messages = [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
        raw = self._complete(messages)

        try:
            graph = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Mistral returned invalid JSON: %s", raw[:300])
            return {
                "task": user_request,
                "nodes": [
                    {
                        "id": "fallback",
                        "role": "GeneralAgent",
                        "task": user_request,
                        "tools": [],
                    }
                ],
                "edges": [],
                "entry_nodes": ["fallback"],
                "exit_node": "fallback",
                "missing_tools": [],
                "_parse_error": str(exc),
            }

        graph["task"] = user_request
        graph = self._validate_graph(graph, user_request, tools)
        return graph

    def _complete(self, messages: list[dict]) -> str:
        """Call the LLM provider chain (NVIDIA NIM → Mistral) and return the
        raw text content.

        Each provider is retried on rate limits with exponential backoff; on a
        non-retryable error or exhausted retries, we fall through to the next
        provider. A provider that rejects ``response_format=json_object`` is
        retried once without it (the prompt still asks for JSON).
        """
        import time

        last_error: Exception | None = None
        for provider, client in zip(self._providers, self._clients):
            use_json_format = True
            for attempt in range(3):
                try:
                    kwargs: dict = {"model": provider["model"], "messages": messages}
                    if use_json_format:
                        kwargs["response_format"] = {"type": "json_object"}
                    response = client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
                    return _extract_text(response)
                except Exception as exc:  # noqa: BLE001 — normalize across SDK error types
                    last_error = exc
                    err = str(exc).lower()
                    if use_json_format and "response_format" in err:
                        logger.warning(
                            "%s rejected response_format=json_object; retrying without it",
                            provider["name"],
                        )
                        use_json_format = False
                        continue
                    if ("429" in err or "rate" in err or "capacity" in err) and attempt < 2:
                        wait = 2 ** attempt * 2  # 2s, 4s
                        logger.warning(
                            "%s rate limited (attempt %d/3), retrying in %ds",
                            provider["name"], attempt + 1, wait,
                        )
                        time.sleep(wait)
                        continue
                    logger.warning(
                        "planner provider %s failed (%s); falling back to next provider",
                        provider["name"], exc,
                    )
                    break
        raise last_error  # type: ignore[misc]

    def _validate_graph(self, graph: dict, user_request: str, tools: list[dict]) -> dict:
        """Validate and repair common planner output issues."""
        nodes = graph.get("nodes", [])
        if not nodes:
            return {
                "task": user_request,
                "nodes": [{"id": "fallback", "role": "GeneralAgent", "task": user_request, "tools": []}],
                "edges": [],
                "entry_nodes": ["fallback"],
                "exit_node": "fallback",
                "missing_tools": graph.get("missing_tools", []),
            }

        nodes = [n for n in nodes if isinstance(n, dict) and "id" in n]
        if not nodes:
            return {
                "task": user_request,
                "nodes": [{"id": "fallback", "role": "GeneralAgent", "task": user_request, "tools": []}],
                "edges": [],
                "entry_nodes": ["fallback"],
                "exit_node": "fallback",
                "missing_tools": graph.get("missing_tools", []),
            }
        graph["nodes"] = nodes

        node_ids = {n["id"] for n in nodes}
        registered_ids = {t["id"] for t in tools}

        for node in nodes:
            if "tools" not in node:
                node["tools"] = []
            valid_tools = [tid for tid in node["tools"] if tid in registered_ids]
            invalid_tools = [tid for tid in node["tools"] if tid not in registered_ids]
            if invalid_tools:
                logger.warning("Planner referenced non-existent tools: %s — dropping them", invalid_tools)
            node["tools"] = valid_tools

        valid_edges = [
            e for e in graph.get("edges", [])
            if isinstance(e, (list, tuple)) and len(e) >= 2 and e[0] in node_ids and e[1] in node_ids
        ]
        graph["edges"] = valid_edges

        if "exit_node" not in graph or graph["exit_node"] not in node_ids:
            graph["exit_node"] = nodes[-1]["id"]

        if "entry_nodes" not in graph:
            targets = {e[1] for e in valid_edges}
            graph["entry_nodes"] = [n["id"] for n in nodes if n["id"] not in targets]
        else:
            graph["entry_nodes"] = [eid for eid in graph["entry_nodes"] if eid in node_ids]

        return graph
