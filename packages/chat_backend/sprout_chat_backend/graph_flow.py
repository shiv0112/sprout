"""
sprout_chat_backend/graph_flow.py
───────────────────────────────
SproutGraphFlow — Phase 4.

Takes the task graph produced by SproutPlanner and executes it as a
directed AG2 multi-agent workflow.

Architecture
────────────
Each graph node becomes an AG2 AssistantAgent + UserProxyAgent pair.
Tools assigned to a node are fetched from the Sprout registry and wrapped in
HTTP-calling stubs so:
  - Tools run on the Sprout registry, not in this process
  - Any agent on any machine can call the same tools
  - Newly registered tools are available without restart

Execution order
───────────────
A topological sort of the task graph determines the run order.
Upstream node results are injected as context into each downstream node's
initial message, so the final synthesis node sees all partial results.

Flow (example):
  weather_node ──┐
                 ├──→ summary_node → final answer
  currency_node ─┘

  1. weather_node  runs  → "28°C, partly cloudy in Singapore"
  2. currency_node runs  → "100 SGD = 68.48 EUR at 0.6848"
  3. summary_node  runs  with both results → synthesised final answer
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import requests

# AG2 (autogen) does not ship a py.typed marker, so mypy can't follow it.
# We can't fix this upstream — silence the import-untyped warning.
from autogen import AssistantAgent, UserProxyAgent, register_function  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# ── Topological sort (Kahn's algorithm) ───────────────────────────────────────

def _topo_sort(nodes: list[dict], edges: list[list[str]]) -> list[str]:
    node_ids  = [n["id"] for n in nodes]
    in_degree = {nid: 0 for nid in node_ids}
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}

    for src, dst in edges:
        in_degree[dst] += 1
        adj[src].append(dst)

    queue  = [nid for nid, deg in in_degree.items() if deg == 0]
    result = []

    while queue:
        nid = queue.pop(0)
        result.append(nid)
        for neighbor in adj[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return result


# ── Attachment scrubber ──────────────────────────────────────────────────────
#
# Tools like image_generate, satellite_image, website_screenshot, and
# generate_qr return base64 data URLs that can be 1-3 MB each. If those land
# in the LLM context they (a) blow Mistral's request size and trigger 429s,
# (b) burn token budget on every subsequent turn. We scrub them out into a
# side-channel `attachments` map keyed by a stable hash, replace the value
# with a `<<image:att_xxx>>` marker, and stream the full payload separately
# via the SSE `attachment` event so the UI can render it client-side.

_DATA_URL_RE = re.compile(r"^data:(image/[\w.+-]+);base64,", re.IGNORECASE)
_ATTACHMENT_KEYS = {"data_url", "image", "screenshot", "thumbnail", "qr_data_url", "preview"}


def _make_attachment_id(data_url: str) -> str:
    digest = hashlib.sha1(data_url.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"att_{digest}"


def _is_data_url(value: Any) -> bool:
    return isinstance(value, str) and bool(_DATA_URL_RE.match(value))


def _scrub_attachments(
    value: Any,
    attachments: dict[str, dict],
    new_attachments: list[dict],
    *,
    node_id: str,
    tool_name: str,
    parent_meta: dict | None = None,
) -> Any:
    """Recursively walk a tool result; replace data URLs with marker strings.

    Mutates `attachments` (the per-run map) and appends new attachment
    descriptors to `new_attachments` so the caller can emit SSE events for
    only the *newly* extracted ones.
    """
    if _is_data_url(value):
        att_id = _make_attachment_id(value)
        if att_id not in attachments:
            mime_match = _DATA_URL_RE.match(value)
            mime = mime_match.group(1) if mime_match else "image/png"
            descriptor = {
                "id": att_id,
                "kind": "image",
                "mime": mime,
                "data_url": value,
                "bytes": _approx_bytes_from_data_url(value),
                "node_id": node_id,
                "tool": tool_name,
                "meta": (parent_meta or {}).copy(),
            }
            attachments[att_id] = descriptor
            new_attachments.append(descriptor)
        return f"<<image:{att_id}>>"

    if isinstance(value, dict):
        # Capture sibling metadata (prompt, model, seed, file_path, etc.) so
        # the attachment carries useful context downstream.
        sibling_meta = {
            k: v
            for k, v in value.items()
            if k in {"prompt", "model", "seed", "file_path", "url", "width", "height", "location", "title"}
            and not _is_data_url(v)
            and isinstance(v, (str, int, float, bool))
        }
        out: dict[str, Any] = {}
        produced_attachment = False
        for k, v in value.items():
            out[k] = _scrub_attachments(
                v, attachments, new_attachments,
                node_id=node_id, tool_name=tool_name,
                parent_meta=sibling_meta,
            )
            if k in _ATTACHMENT_KEYS and isinstance(out[k], str) and out[k].startswith("<<image:"):
                # Promote the sibling meta onto the descriptor we just created.
                marker = out[k]
                att_id = marker[len("<<image:"):-2]
                if att_id in attachments:
                    attachments[att_id]["meta"].update(sibling_meta)
                produced_attachment = True

        # If this dict produced at least one image attachment, strip the
        # internal plumbing keys before the LLM sees it. The UI gets these
        # via the attachment descriptor's meta — the LLM doesn't need them
        # and tends to leak them into the user-facing answer ("File path:
        # /tmp/...", "Base64 data URL: ..."). Keeping just `success` and
        # the marker-bearing key keeps the LLM focused on the marker.
        if produced_attachment:
            out = {
                k: v
                for k, v in out.items()
                if k in _ATTACHMENT_KEYS
                or k in {"success", "error", "prompt"}
                or (isinstance(v, str) and v.startswith("<<image:"))
            }

        return out

    if isinstance(value, list):
        return [
            _scrub_attachments(
                item, attachments, new_attachments,
                node_id=node_id, tool_name=tool_name,
                parent_meta=parent_meta,
            )
            for item in value
        ]

    return value


def _approx_bytes_from_data_url(data_url: str) -> int:
    try:
        b64 = data_url.split(",", 1)[1]
        return int(len(b64) * 3 / 4)
    except Exception:
        return 0


# ── SproutToolBridge ───────────────────────────────────────────────────────────

def _make_http_tool(
    tool_id: str,
    spec: dict,
    server_url: str,
    node_id: str = "",
    on_event=None,
    env_vars: dict[str, str] | None = None,
    attachments: dict[str, dict] | None = None,
):
    """
    Create an AG2-compatible Python callable that runs a Sprout tool
    via HTTP POST to the Sprout registry.

    Dynamically builds a function with the exact signature (name,
    typed parameters, defaults) so AG2's inspect.signature() works.
    Emits tool_call and tool_result events if on_event is provided.
    """
    name   = spec["name"]
    params = spec["params"]

    _TYPE_MAP = {
        "str": "str", "int": "int", "float": "float",
        "bool": "bool", "list": "list", "dict": "dict",
    }

    sig_parts = []
    for p in params:
        t = _TYPE_MAP.get(p["type"], "str")
        if not p["required"] and p["default"] is not None:
            sig_parts.append(f"{p['name']}: {t} = {repr(p['default'])}")
        elif not p["required"]:
            sig_parts.append(f"{p['name']}: {t} = None")
        else:
            sig_parts.append(f"{p['name']}: {t}")

    sig_str    = ", ".join(sig_parts)
    kwargs_str = ", ".join(f'"{p["name"]}": {p["name"]}' for p in params)

    fn_source = (
        f"def {name}({sig_str}) -> str:\n"
        f"    \"\"\"{spec['description']}\"\"\"\n"
        f"    resp = _http_call(_server_url, _tool_id, {{{kwargs_str}}})\n"
        f"    return resp\n"
    )

    _env_vars = env_vars or {}

    def _http_call(url: str, tid: str, args: dict) -> Any:
        if on_event:
            on_event({"type": "tool_call", "node_id": node_id, "tool": name, "args": args})
        try:
            payload: dict[str, Any] = {"args": args}
            if _env_vars:
                payload["env_vars"] = _env_vars
            r = requests.post(
                f"{url}/tools/{tid}/execute",
                json=payload,
                headers={"X-Internal-Secret": os.environ.get("SPROUT_INTERNAL_SECRET", "")},
                timeout=30,
            )
            r.raise_for_status()
            result = r.json()["result"]
        except requests.Timeout:
            result = {"error": f"Tool '{name}' timed out after 30s"}
        except requests.ConnectionError:
            result = {"error": f"Tool '{name}' unreachable — registry may be down"}
        except requests.HTTPError as e:
            detail = ""
            try:
                detail = e.response.json().get("detail", "")
            except Exception:
                detail = e.response.text[:200] if e.response else ""
            result = {"error": f"Tool '{name}' HTTP {e.response.status_code}: {detail}"}
        except Exception as e:
            result = {"error": f"Tool '{name}' failed: {e}"}
        # Scrub heavy binary payloads (data URLs) before they hit the LLM
        # context. The summarizer node would otherwise receive the full
        # base64 payload and (a) blow Mistral's request size, (b) bleed
        # tokens. We replace each data URL with a stable marker that the
        # frontend re-hydrates via the SSE `attachment` event.
        if attachments is not None:
            new_attachments: list[dict] = []
            result = _scrub_attachments(result, attachments, new_attachments, node_id=node_id, tool_name=name)
            if on_event:
                for att in new_attachments:
                    on_event({"type": "attachment", **att})
        if on_event:
            on_event({"type": "tool_result", "node_id": node_id, "tool": name, "result": result})
        # AG2 feeds the return value straight into the model's context, so hand
        # it clean text (a JSON string), not a bare dict. Returning a dict makes
        # AG2 warn and the model can't read the values — it ends up describing
        # the tool instead of reporting the result (e.g. "12 people in space").
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)

    namespace = {
        "_http_call":  _http_call,
        "_server_url": server_url,
        "_tool_id":    tool_id,
    }
    # Use compile + exec to build the callable with the correct signature
    code = compile(fn_source, f"<sprout_tool_{name}>", "exec")
    _dynamic_exec(code, namespace)
    return namespace[name]


def _dynamic_exec(code, namespace):
    """Execute compiled code in namespace to create the tool function."""
    # This indirection exists so that the dynamic function creation is
    # isolated into its own helper for clarity.
    exec(code, namespace)  # noqa: S102


# ── Result extraction ─────────────────────────────────────────────────────────

def _extract_result(node_id: str, chat_history: list[dict]) -> str:
    """
    Pull the last substantive message from the AssistantAgent in this node.

    AG2 stores the UserProxyAgent's own messages as role='assistant' and the
    AssistantAgent's messages as role='user' from the executor's perspective.
    Filtering by agent name (stored in the 'name' field) is reliable.
    """
    target_name = f"{node_id}_assistant"

    # First pass: look for messages by the named assistant agent
    for msg in reversed(chat_history):
        if target_name not in (msg.get("name") or ""):
            continue
        content = _sanitize_agent_output(msg.get("content") or "")
        if content:
            return content

    # Fallback: last non-empty, non-tool message
    for msg in reversed(chat_history):
        if msg.get("role") == "tool":
            continue
        content = _sanitize_agent_output(msg.get("content") or "")
        if content:
            return content

    return "(no result)"


_TOOL_TRANSCRIPT_MARKERS = (
    "TOOL CALL",
    "TOOL RESPONSE",
)


def _looks_like_tool_json(line: str) -> bool:
    """Return True only for AG2 internal tool-call/response JSON, not data."""
    stripped = line.strip()
    if not stripped:
        return False
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return False
    return '"tool_name"' in stripped and '"args"' in stripped


def _sanitize_agent_output(content: str) -> str:
    """Strip AG2 tool transcript noise from assistant-visible node results."""
    text = content.replace("TERMINATE", "").strip()
    if not text:
        return ""

    if not any(marker in text for marker in _TOOL_TRANSCRIPT_MARKERS):
        return text

    kept_lines: list[str] = []
    in_fence = False
    in_tool_fence = False
    in_tool_section = False
    pending_fence: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                in_tool_fence = in_tool_section or any(m in text for m in _TOOL_TRANSCRIPT_MARKERS)
                if not in_tool_fence:
                    pending_fence = [line]
            else:
                in_fence = False
                if not in_tool_fence:
                    pending_fence.append(line)
                    has_tool_json = any(
                        fl.strip().startswith("{") and '"tool_name"' in fl
                        for fl in pending_fence
                    )
                    if not has_tool_json:
                        kept_lines.extend(pending_fence)
                    pending_fence = []
                in_tool_fence = False
            continue

        if not stripped:
            in_tool_section = False
            if in_fence and not in_tool_fence:
                pending_fence.append("")
            elif not in_fence and kept_lines and kept_lines[-1] != "":
                kept_lines.append("")
            continue

        if any(marker in stripped for marker in _TOOL_TRANSCRIPT_MARKERS):
            in_tool_section = True
            continue

        if in_fence:
            if not in_tool_fence:
                pending_fence.append(line)
            continue

        if in_tool_section and stripped.startswith(("{", "[")):
            continue

        if _looks_like_tool_json(stripped):
            continue

        if re.fullmatch(r"Let me proceed\.?", stripped, flags=re.IGNORECASE):
            in_tool_section = False
            continue

        in_tool_section = False
        kept_lines.append(stripped)

    sanitized = "\n".join(kept_lines)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    return sanitized or text


# ── SproutGraphFlow ─────────────────────────────────────────────────────────────

class SproutGraphFlow:
    """
    Phase 4: executes a task graph as a directed AG2 multi-agent workflow.

    Usage:
        flow = SproutGraphFlow(
            registry_url     = "http://localhost:8766",
            llm_config       = {"config_list": [...], "cache_seed": None},
        )
        result = flow.run(task_graph, verbose=True)
    """

    def __init__(
        self,
        registry_url: str = "http://localhost:8766",
        llm_config: dict | None = None,
        on_event=None,
    ):
        self._server_url = registry_url.rstrip("/")
        self._llm_config = llm_config or {}
        self._tool_cache: dict[str, dict] = {}   # tool_id → spec dict
        self._on_event   = on_event              # callable(event_dict) | None
        self._attachments: dict[str, dict] = {}  # att_id → attachment descriptor

    def _emit(self, event_type: str, **data) -> None:
        if self._on_event:
            self._on_event({"type": event_type, **data})

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, task_graph: dict, extra_env: dict[str, str] | None = None, verbose: bool = True) -> str:
        """
        Execute the task graph and return the final synthesised answer.

        Args:
            task_graph: Dict produced by SproutPlanner.plan()
            extra_env:  Optional dict of env vars to inject for this run only
                        (e.g. API keys supplied by the user via the UI).
            verbose:    Print node-by-node execution progress

        Returns:
            Final answer string from the exit node.
        """
        self._extra_env = extra_env or {}

        # Also inject into os.environ for any local checks (e.g. _collect_missing_envs)
        _saved: dict[str, str | None] = {}
        if extra_env:
            for k, v in extra_env.items():
                _saved[k] = os.environ.get(k)
                os.environ[k] = v

        try:
            return self._run_graph(task_graph, verbose)
        finally:
            self._extra_env = {}
            for k, original in _saved.items():
                if original is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = original

    # ── Failure detection ──────────────────────────────────────────────────────

    _FAILURE_PATTERNS = (
        '"success": false',
        "'success': false",
        "(no result)",
        "(error:",
        "no wikipedia article found",
        "could not find",
        "failed to",
        "failed due to",
        "unable to complete",
        "unable to fetch",
        "api key not set",
        "api key is missing",
        "api key not found",
        "missing news_api_key",
        "missing api key",
        "missing api_key",
        "authentication issue",
        "authentication failed",
        "authentication error",
        "unauthorized",
        "invalid api key",
        "error occurred",
        "no results found",
        "not found for",
        "timed out",
        "unreachable",
        '"error":',
        "rate limit",
        "403 forbidden",
        "401 unauthorized",
        "not available in the registry",
        "not available in the current registry",
        "none of the available tools",
        "no tools in the registry",
        "environment variable not set",
        "i cannot provide",
        "cannot be completed",
        "cannot complete",
    )

    def _is_failure(self, result: str) -> bool:
        """Return True if the node result looks like a tool/task failure."""
        lower = result.lower()
        return any(p in lower for p in self._FAILURE_PATTERNS)

    def _run_graph(self, task_graph: dict, verbose: bool) -> str:
        """Internal: execute nodes in topological order."""
        nodes     = {n["id"]: n for n in task_graph["nodes"]}
        edges     = task_graph.get("edges", [])
        exit_node = task_graph["exit_node"]
        order     = _topo_sort(list(nodes.values()), edges)

        if len(order) != len(nodes):
            missing = set(nodes.keys()) - set(order)
            logger.error("Graph has a cycle or disconnected nodes: %s not reachable", missing)
            self._emit("error", message=f"Task graph has a cycle involving nodes: {missing}")
            return f"(error: task graph has a cycle involving {missing})"

        if verbose:
            self._print_graph(task_graph, order)

        # Fetch all tool specs we'll need upfront (one batch GET)
        self._warm_tool_cache(task_graph["nodes"])

        # Execute nodes in topological order, accumulating context
        context: dict[str, str] = {}   # node_id → result text

        for node_id in order:
            node = nodes[node_id]
            self._emit(
                "node_start",
                node_id=node_id,
                role=node["role"],
                tools=node.get("tools", []),
                task=node.get("task", ""),
            )
            if verbose:
                logger.info(f"Running node: [{node_id}]  role={node['role']}")
                logger.info(f"Tools: {node.get('tools', []) or '(none)'}")

            result = self._run_node_with_backoff(
                node, context, task_graph["task"]
            )

            # ── Retry once if a non-exit node failed ─────────────────────────
            if node_id != exit_node and self._is_failure(result):
                self._emit("node_retry", node_id=node_id, reason=result[:300])
                if verbose:
                    logger.info(f"Node '{node_id}' failed — retrying with enriched prompt")
                retry_node = {
                    **node,
                    "task": (
                        f"{node.get('task', task_graph['task'])}\n\n"
                        "IMPORTANT: Your previous attempt encountered a problem:\n"
                        f"  {result[:300]}\n\n"
                        "Please try an alternative approach:\n"
                        "- Use different, broader or more specific search terms\n"
                        "- Break a complex query into simpler sub-queries\n"
                        "- If one tool fails, try another available tool\n"
                        "- If ALL tools fail, say so honestly — do NOT make up data"
                    ),
                }
                result = self._run_node_with_backoff(
                    retry_node, context, task_graph["task"]
                )

            context[node_id] = result
            self._emit("node_complete", node_id=node_id, result=result)

            if verbose:
                logger.info(f"Result → {result[:200]}{'...' if len(result) > 200 else ''}")

        final = context.get(exit_node, "(no result)")

        # Safety net: if any attachments were produced this run but the final
        # answer doesn't reference them in any recognised form, append the
        # markers ourselves so the UI can still render them. The frontend
        # rewriter normalises every form to the canonical image markdown.
        if self._attachments:
            referenced = any(
                (f"<<image:{aid}>>" in final) or (aid in final)
                for aid in self._attachments
            )
            if not referenced:
                tail = "\n\n" + "\n\n".join(
                    f"<<image:{aid}>>" for aid in self._attachments
                )
                final = final.rstrip() + tail

        return final

    # ── Node execution ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        err_str = str(exc).lower()
        return (
            "429" in err_str
            or "rate limit" in err_str
            or "rate_limited" in err_str
            or "capacity" in err_str
        )

    def _run_node_with_backoff(
        self, node: dict, context: dict[str, str], original_task: str
    ) -> str:
        """Run a node, retrying on LLM rate limits with exponential backoff.

        Non-rate-limit exceptions are surfaced immediately as failure strings,
        so the caller's generic retry path can kick in.
        """
        node_id = node["id"]
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return self._run_node(node, context, original_task)
            except Exception as exc:
                last_exc = exc
                if self._is_rate_limit_error(exc) and attempt < 2:
                    wait = 2 ** attempt * 2  # 2s, 4s
                    logger.warning(
                        "Node '%s' hit LLM rate limit (attempt %d/3), retrying in %ds",
                        node_id, attempt + 1, wait,
                    )
                    self._emit("node_retry", node_id=node_id, reason=f"rate limited; waiting {wait}s")
                    time.sleep(wait)
                    continue
                break
        logger.error("Node '%s' raised an exception: %s", node_id, last_exc)
        return f"(error: node '{node_id}' crashed: {last_exc})"

    def _run_node(self, node: dict, context: dict[str, str], original_task: str) -> str:
        """Build an AG2 agent pair for this node, register its tools, run it."""
        node_id   = node["id"]
        role      = node["role"]
        node_task = node.get("task", original_task)
        tool_ids  = node.get("tools", [])

        # ── System message ────────────────────────────────────────────────────
        # Inject the FULL Sprout registry as a reference list. Even agents with
        # no assigned tools (SHAPE A meta questions) need this so they answer
        # accurately about what tools exist instead of fabricating names.
        registry_summary = self._registry_summary()
        system_msg = (
            f"You are {role}, an agent in the Sprout multi-agent workflow.\n"
            f"Your specific task: {node_task}\n\n"
            "Sprout is a self-hosted, self-evolving tool registry for AI agents. "
            "The registry currently contains these tools "
            "(id, category, tags, description):\n"
            f"{registry_summary}\n\n"
            "Rules for answering questions about Sprout tools:\n"
            "1. List ONLY tools whose id appears verbatim in the list above. "
            "Never invent tool names like fetch_webpage or extract_html_structured_data.\n"
            "2. **Match the user's intent strictly.** A user asking for 'web scraping tools' "
            "wants tools that fetch arbitrary web pages and parse HTML — NOT a "
            "domain-specific parser like cricket_data_parser or fed_policy_parser even though "
            "those technically 'parse text'. Read the description carefully and pick only the "
            "tools that genuinely fit. Look at the category and tags too — those are stronger "
            "signals than keyword-matching the description.\n"
            "3. **If no tools in the registry actually match the user's request, say so "
            "honestly in one sentence.** Suggest the user describe what they need so "
            "Sprout can synthesize it. Do NOT pad the answer with tangentially-related tools.\n"
            "4. When you do list relevant tools, include 1-5 max. Quality over quantity.\n\n"
            "General rules:\n"
            "- Use the tools registered with you (if any) to complete your task. Do not "
            "describe tools you weren't given access to as if you can call them.\n"
            "- Be concise and factual. Do not fabricate URLs, docs, or product names.\n"
            "- CRITICAL: If your task requires fetching real-time data (prices, weather, "
            "news, exchange rates, etc.) and you have NO tools to do so AND it is not "
            "already in the upstream context, say \"I don't have a tool to fetch this "
            "data\" — do NOT make up numbers, dates, or facts from your training data. "
            "Fabricated data is WORSE than no data.\n"
            "- When a tool returns a result, REPORT THE ACTUAL VALUES from it in your "
            "reply (e.g. 'The temperature in Tokyo is 15°C'). Downstream agents and the "
            "user see ONLY your reply text, never the raw tool output — so never just "
            "describe the tool, repeat your task, or ask the user for data a tool "
            "already returned.\n"
            "- If the data you need is ALREADY in the 'Context from upstream agents' "
            "section above, USE it directly (e.g. convert/summarize it). Do NOT claim "
            "you lack a tool for something an upstream agent already provided.\n"
            "- If an upstream agent FAILED (context marked as FAILED), acknowledge the "
            "failure explicitly. Report what went wrong and suggest a fix (e.g. 'the news "
            "API returned an authentication error — the user may need to provide a valid "
            "NEWS_API_KEY'). Never pretend you have data when the upstream failed.\n"
            "- Image / file attachments: when a tool result contains a value "
            "like `<<image:att_xxx>>`, that token IS the image. Rules — read "
            "carefully, every word matters:\n"
            "    * Output the token EXACTLY as `<<image:att_xxx>>` — preserve "
            "the angle brackets, the `image:` prefix, and the `att_` id "
            "lowercase. Do NOT change capitalisation. Do NOT URL-encode it. "
            "Do NOT rewrite it as `sprout-att://...`. Do NOT wrap it in "
            "`![alt](...)` markdown syntax. The token alone is sufficient.\n"
            "    * Place the token on its own line where you want the image "
            "to appear. The UI converts it into a real rendered image with "
            "caption, download button, and zoom — you do not need to add any "
            "of that yourself.\n"
            "    * Do NOT mention `data_url`, `file_path`, `base64`, "
            "`embedding`, the attachment id itself, or how the UI handles it. "
            "The user sees a polished image, not the plumbing.\n"
            "    * Do NOT describe what the image looks like — they can see "
            "it. A short caption like 'Here is the image you asked for:' "
            "followed by the token on its own line is ideal.\n"
            "  Correct example:\n"
            "    Here is the sun you asked for:\n\n"
            "    <<image:att_abc123>>\n"
            "  Wrong examples (do NOT do these):\n"
            "    ![Sun](sprout-att://att_abc123)\n"
            "    ![[Sprout-att://att_abc123]](att_abc123)\n"
            "    The base64 data URL has been embedded via the att_abc123 token.\n"
            "- Reply TERMINATE when done."
        )

        # ── Create AG2 agent pair ─────────────────────────────────────────────
        assistant = AssistantAgent(
            name=f"{node_id}_assistant",
            llm_config=self._llm_config,
            system_message=system_msg,
            is_termination_msg=lambda m: "TERMINATE" in (m.get("content") or ""),
        )
        executor = UserProxyAgent(
            name=f"{node_id}_executor",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=8,
            code_execution_config=False,
            is_termination_msg=lambda m: "TERMINATE" in (m.get("content") or ""),
        )

        # ── Clean messages before LLM call (Mistral compatibility) ────────────
        # Mistral rejects: (1) 'name' field, (2) assistant messages with
        # content=None and no tool_calls.
        def _clean_messages(messages):
            cleaned = []
            for m in messages:
                m = {k: v for k, v in m.items() if k != "name"}
                # Mistral requires content OR tool_calls on assistant messages
                if m.get("role") == "assistant" and not m.get("content") and not m.get("tool_calls"):
                    m = {**m, "content": ""}
                cleaned.append(m)
            return cleaned

        assistant.register_hook("process_all_messages_before_reply", _clean_messages)

        # ── Register Sprout tools via HTTP bridge ──────────────────────────────
        for tool_id in tool_ids:
            spec = self._tool_cache.get(tool_id)
            if spec is None:
                logger.warning(f"Tool '{tool_id}' not found on Sprout registry — skipping")
                continue
            fn = _make_http_tool(
                tool_id, spec, self._server_url,
                node_id=node_id, on_event=self._on_event,
                env_vars=self._extra_env,
                attachments=self._attachments,
            )
            register_function(
                fn,
                caller=assistant,
                executor=executor,
                name=spec["name"],
                description=spec["description"],
            )

        # ── Build initial message ─────────────────────────────────────────────
        initial_msg = self._build_message(node_task, context)

        # ── Run ───────────────────────────────────────────────────────────────
        chat_result = executor.initiate_chat(
            assistant,
            message=initial_msg,
            silent=self._on_event is not None,  # silent when streaming to UI
            max_turns=10,
        )

        return _extract_result(node_id, chat_result.chat_history)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _build_message(self, node_task: str, context: dict[str, str]) -> str:
        """
        Build the initial message for a node, injecting upstream results
        as context so downstream agents have full information.

        Explicitly marks failed upstream results so downstream agents report
        the failure honestly instead of hallucinating around missing data.
        """
        msg = node_task
        if context:
            parts: list[str] = []
            has_failures = False
            for node_id, result in context.items():
                if self._is_failure(result):
                    has_failures = True
                    parts.append(f"  [{node_id}] FAILED: {result}")
                else:
                    parts.append(f"  [{node_id}]: {result}")

            upstream = "\n".join(parts)
            msg += f"\n\nContext from upstream agents:\n{upstream}"
            if "<<image:" in upstream:
                msg += (
                    "\n\nThe upstream context contains one or more "
                    "`<<image:att_...>>` tokens. Each token represents a real "
                    "image attachment that the UI will render. Output each "
                    "token EXACTLY as it appears — angle brackets, "
                    "`image:` prefix, lowercase `att_` id. Place the token "
                    "on its own line. Do NOT wrap it in markdown image "
                    "syntax `![](...)`. Do NOT rewrite it as `sprout-att://`. "
                    "Do NOT invent new ids. Do NOT mention base64, "
                    "data URLs, file paths, or how the embedding works. "
                    "Do NOT describe what the image looks like — the user "
                    "can see it. A short caption then the bare token on "
                    "its own line is ideal."
                )
            if has_failures:
                msg += (
                    "\n\nIMPORTANT: One or more upstream agents FAILED (marked above). "
                    "Do NOT fabricate or guess the missing data. Instead, acknowledge the "
                    "failure and explain what went wrong based on the error message. "
                    "If the upstream failure means you cannot complete your task, say so "
                    "clearly and suggest what the user can do (e.g. provide an API key, "
                    "retry later, etc.)."
                )
        return msg

    def _warm_tool_cache(self, nodes: list[dict]) -> None:
        """Fetch the full registry once per run.

        We cache every registered tool (not just the ones referenced by this
        graph) so that:
          1. _run_node has the specs for the tools it needs to register with AG2
          2. The full registry summary can be injected into the system message
             of every agent — critical for SHAPE A meta questions like
             "what tools exist?" where the planner picked no tools but the
             agent still needs to answer accurately about what's available.
        """
        try:
            resp = requests.get(f"{self._server_url}/tools", timeout=5)
            resp.raise_for_status()
            for tool in resp.json():
                self._tool_cache[tool["id"]] = tool
        except requests.RequestException as e:
            logger.warning(f"Could not fetch tools from Sprout registry: {e}")

    def _registry_summary(self) -> str:
        """One-line-per-tool summary of the full Sprout registry.

        Injected into every agent's system message so meta questions like
        "find tools for web scraping" get answered from the actual registry
        instead of fabricated tool names. Includes id, category, tags, and
        description so the agent can match by category/tag rather than just
        keyword-matching the description.
        """
        if not self._tool_cache:
            return "(registry currently empty)"
        lines = []
        for tool in sorted(self._tool_cache.values(), key=lambda t: t.get("id", "")):
            tid = tool.get("id", "?")
            desc = (tool.get("description") or "").strip().replace("\n", " ")
            if len(desc) > 100:
                desc = desc[:97] + "..."
            category = tool.get("category") or "general"
            tags = tool.get("tags") or []
            tag_str = f" [tags: {', '.join(tags)}]" if tags else ""
            lines.append(f"  - {tid} ({category}){tag_str}: {desc}")
        return "\n".join(lines)

    def _print_graph(self, task_graph: dict, order: list[str]) -> None:
        """Pretty-print the task graph before execution."""
        nodes = {n["id"]: n for n in task_graph["nodes"]}
        logger.info("Sprout Task Graph")
        logger.info(f"Task: {task_graph['task'][:55]}")
        logger.info(f"Execution order: {' → '.join(order)}")
        logger.info(f"Edges: {task_graph.get('edges', [])}")
        if task_graph.get("missing_tools"):
            logger.info(f"Missing tools:   {task_graph['missing_tools']}")
        for nid in order:
            n = nodes[nid]
            marker = "EXIT" if nid == task_graph["exit_node"] else "    "
            logger.info(f"[{marker}] {nid:20s}  role={n['role']}")
            logger.info(f"        tools={n.get('tools', []) or '(none)'}")
            logger.info(f"        task={n.get('task', '')[:55]}")
