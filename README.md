# Sprout

**A self-evolving tool registry for AI agents.**

Sprout is a platform where AI agents discover, execute, and — when a tool doesn't
exist yet — **synthesize new tools on the fly**. Ask for something in natural
language, and Sprout figures out how to do it. No restarts, no hand-wired
integrations.

### ▶ Live: **https://sprout-tool-registry.vercel.app/**

---

## The Problem

Every AI agent framework (LangChain, AG2, CrewAI, Anthropic's MCP) requires you
to pre-define tools before an agent can use them. Need a new tool? Stop the
agent, write the code, register it, restart. This creates a hard ceiling: agents
are only as capable as the tools you've already built.

## The Solution

Sprout removes the ceiling:

1. **You ask** something in natural language via the chat UI, any MCP client
   (Claude Desktop, Cursor, VS Code Copilot), or the registry HTTP API.
2. **ARIA** (the planning agent) decomposes your request into a directed task graph.
3. If a required tool **doesn't exist**, ARIA triggers **Vibe** (a coding agent) to
   synthesize it — spec + implementation + tests — in seconds.
4. The new tool is **hot-loaded** into the registry. No restart.
5. ARIA continues execution with the freshly created tool, and MCP clients see the
   new tool appear automatically.

```
"What's the weather in Tokyo and convert it to Fahrenheit?"

  ARIA: I need weather_lookup (exists) and temp_converter (missing)
    -> Vibe synthesizes temp_converter in ~15 seconds
    -> Tool registered, tested, hot-loaded
    -> ARIA executes the full plan
    -> Answer delivered
```

---

## Architecture

```
                              Clients
   Browser (Next.js UI)              MCP clients (Claude Desktop, Cursor, …)
        │ Clerk JWT                        │ OAuth 2.1 + PKCE
        ▼                                  ▼
   ┌──────────────┐                 ┌──────────────┐
   │ chat_backend │                 │  mcp_server  │
   │   planner    │                 │  MCP bridge  │
   └──────┬───────┘                 └──────┬───────┘
          │  tool execution (HTTP)         │
          ▼                                ▼
   ┌───────────────────────────────────────────────┐
   │                 registry_api                    │
   │      tool CRUD · search · execute · auth        │
   └───────┬───────────────────────────────┬─────────┘
           ▼                                ▼
   ┌────────────────┐             ┌────────────────────┐
   │  tool_executor │             │  synthesis (Vibe)  │
   │  sandboxed run │             │   OpenCode + LLM   │
   └────────────────┘             └─────────┬──────────┘
                                            │ webhook on completion
                                            ▼
                                   registry/tools/ on disk

           PostgreSQL · Redis   (metadata, cache, rate limiting)
```

### How it works

- **Planning → DAG.** `chat_backend` turns your request into a task graph, then
  runs it as a topological DAG of AG2 (PyAutoGen) agents, streaming progress back
  over SSE.
- **HTTP tool proxy.** Agents never import tools directly — they call HTTP stubs
  that hit `registry_api`. This is what makes hot-reload, distributed execution,
  and per-tool access control possible.
- **Spec-driven tools.** Every tool is a framework-agnostic `spec.yaml` + `impl.py`.
  Compilers translate one spec into AG2, Mistral, LangChain, or Pydantic AI tool
  definitions on demand — hand-written and synthesized tools work everywhere.
- **On-the-fly synthesis.** When a tool is missing, `synthesis_service` runs the
  Vibe coding agent (OpenCode) to write the spec, implementation, and tests, then
  webhooks the result back to the registry, which hot-loads it.

### Services

| Service | Port | Responsibility |
|---------|------|----------------|
| `registry_api` | 8766 | Tool registry: CRUD, search, execution, auth |
| `chat_backend` | 8765 | Planner → task DAG → AG2 multi-agent executor → SSE stream |
| `synthesis_service` | 8002 | Vibe: OpenCode generates `spec.yaml` + `impl.py`, calls back to registry |
| `mcp_server` | 8768 | MCP bridge + OAuth 2.1 / PKCE authorization server |
| `tool_executor` | 8767 | Sandboxed tool execution |
| `registry_ui` | 3001 | Next.js 16 + React 19 frontend (chat, catalog, publish, settings) |
| `postgres` / `redis` | — | Tool metadata, cache, per-user rate limiting |

**Planning LLM chain:** Groq (`llama-3.3-70b-versatile`) → NVIDIA NIM → Mistral,
tried in order with automatic fallback. Tool synthesis uses OpenCode with a coding
model (Mistral Codestral by default).

---

## Tool Format

Every tool is a pair of files on disk:
`registry/tools/{id}/{version}/spec.yaml` + `{entrypoint}.py`.

**`spec.yaml`** — what the tool does:
```yaml
sprout_version: "1.0"
tool:
  id: com.sprout.tools.fetch_url
  name: fetch_url
  version: "1.0.0"
  description: "Fetch any public URL and return plain-text content with HTML stripped."
  author: aria
interface:
  inputs:
    - name: url
      type: string
      required: true
    - name: max_chars
      type: integer
      required: false
      default: 3000
  outputs:
    - name: title
      type: string
    - name: content
      type: string
implementation:
  runtime: python3.10
  entrypoint: fetch_url.py
  dependencies:
    - requests>=2.28.0
    - beautifulsoup4>=4.12.0
metadata:
  tags: [web, scraping]
  category: research
```

**`{entrypoint}.py`** — how it works:
```python
REQUIRED_ENV_VARS = []  # env vars the tool needs to run

def fetch_url(**kwargs) -> dict:
    url = kwargs["url"]
    # ... actual implementation ...
    return {"title": "...", "content": "...", "success": True}
```

The format is framework-agnostic — Sprout's `compiler/` module translates specs to
AG2, Mistral, LangChain, or Pydantic AI on the fly, so any tool works with any
supported framework without modification.

---

## MCP Support

Sprout exposes every registered tool over the
[Model Context Protocol](https://modelcontextprotocol.io/) via `mcp_server`.
Standard MCP clients connect and use Sprout tools directly.

- **Auth**: OAuth 2.1 with PKCE. The MCP server is itself the authorization server;
  Clerk provides user identity. Dynamic client registration (RFC 7591) is enabled.
- **User context**: a signed-in user's saved tool env vars (stored in Clerk
  `private_metadata`) are injected into tool execution automatically.
- **Hot refresh**: newly synthesized tools become available to MCP clients through
  polling — no reconnect needed.

Point any MCP client at the `/mcp` endpoint of your Sprout server to connect.

---

## Run it locally

### With Docker (recommended)

**Prerequisites**
- Docker & Docker Compose
- A [Groq API key](https://console.groq.com) (planner / agent execution)
- A [Mistral API key](https://console.mistral.ai/) (tool synthesis via Codestral)
- A [Clerk](https://dashboard.clerk.com) project (publishable + secret keys)
- *Optional:* an [NVIDIA NIM key](https://build.nvidia.com/) as an extra fallback

```bash
git clone https://github.com/shiv0112/sprout.git
cd sprout
cp .env.example .env
# Fill in .env:
#   GROQ_API_KEY, MISTRAL_API_KEY
#   CLERK_DOMAIN, CLERK_SECRET_KEY, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
#   SPROUT_INTERNAL_SECRET  (python -c "import secrets; print(secrets.token_hex(32))")

./dev.sh          # builds + starts everything via Docker Compose (hot-reload)
```

Then:
- **Web UI** — http://localhost:3001
- **Registry API** — http://localhost:8766
- **MCP server** — http://localhost:8768/mcp

### Without Docker

```bash
uv sync                                          # install all Python deps
cd packages/registry_ui && pnpm install && cd ../..

# each in its own terminal
uv run uvicorn sprout_registry.main:app --port 8766 --reload
uv run uvicorn sprout_chat_backend.main:app --port 8765 --reload
uv run uvicorn sprout_synthesis.main:app --port 8002
uv run python -m sprout_mcp.main streamable-http
cd packages/registry_ui && pnpm run dev
```

### Tests & lint

```bash
uv run pytest                                    # Python tests
uv run ruff check packages/                      # lint
uv run mypy packages/                            # type check
cd packages/registry_ui && pnpm run lint         # frontend lint (zero warnings)
```

---

## Repository Layout

```
sprout/
├── packages/
│   ├── shared/              # sprout_shared — models, auth, config, CORS, rate-limit
│   ├── registry_api/        # sprout_registry — tool CRUD, search, execute, auth
│   ├── chat_backend/        # sprout_chat_backend — planner, DAG executor, SSE
│   ├── synthesis_service/   # sprout_synthesis — Vibe (OpenCode) tool synthesis
│   ├── tool_executor/       # sprout_executor — sandboxed execution
│   ├── mcp_server/          # sprout_mcp — MCP bridge + OAuth 2.1 AS
│   └── registry_ui/         # Next.js 16 / React 19 / Tailwind 4 / Clerk
├── registry/tools/          # registered tools (spec.yaml + impl.py per version)
├── docker-compose.yml       # local dev stack
└── docs/                    # setup & deployment guides
```

---

## Tech Stack

| Layer | Stack |
|-------|-------|
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.0, asyncpg/aiosqlite |
| LLM | Groq (planning) → NVIDIA NIM → Mistral (fallback); OpenCode + Codestral (synthesis) |
| Multi-agent | AG2 (PyAutoGen) |
| Frontend | Next.js 16, React 19, TypeScript 5.9, Tailwind 4, Clerk, Vercel AI SDK, TanStack Query |
| Auth | Clerk (JWT for browser, API keys for CLI, OAuth 2.1 for MCP clients) |
| MCP | Anthropic MCP SDK, streamable HTTP transport |
| Infra | Docker Compose, PostgreSQL 17, Redis 7; uv + pnpm + Nx |

---

## Security

- **Synthesized code is AST-validated** before it can run — dangerous imports and
  builtins (shell execution, unsafe deserializers, raw sockets, GUI automation, …)
  are rejected at registration and execution time.
- **Per-tool env vars are explicit** — each tool declares its `REQUIRED_ENV_VARS`;
  keys are stored in Clerk `private_metadata` and injected only at execution.
- **OAuth 2.1 + PKCE** for MCP clients, **API keys** for CLI, **Clerk JWT** for the browser.
- **Service-to-service auth** via an internal secret, plus per-user rate limiting
  and a strict CORS allowlist.

---

## Deployment

Sprout is deployed and live at **https://sprout-tool-registry.vercel.app/** — the
frontend on Vercel, the backend services as a single containerized stack. To
self-host the backend with one command, see
[docs/DEPLOY_DROPLET.md](docs/DEPLOY_DROPLET.md).

---

## Contributors

- **[Shivansh](https://github.com/shiv0112)**
- **Jahnvi**

---

## License

Apache 2.0
