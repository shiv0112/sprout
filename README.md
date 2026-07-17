# Kiln

**A self-evolving tool registry for AI agents.**

Kiln is a platform where AI agents discover, execute, and — when a tool doesn't exist yet — synthesize new tools on the fly. Ask for something in natural language, and Kiln figures out how to do it. No restarts, no hand-wired integrations.

---

## The Problem

Every AI agent framework (LangChain, AG2, CrewAI, Anthropic's MCP) requires you to pre-define tools before an agent can use them. Need a new tool? Stop the agent, write the code, register it, restart. This creates a hard ceiling: agents are only as capable as the tools you've already built.

## The Solution

Kiln removes the ceiling:

1. **You ask** something in natural language via the Kiln chat UI, any MCP client (Claude Desktop, ChatGPT, Cursor, VS Code Copilot), or the registry HTTP API.
2. **ARIA** (the planning agent) decomposes your request into a directed task graph.
3. If a required tool **doesn't exist**, ARIA triggers **Vibe** (OpenCode/NIM-backed coding agent) to synthesize it — spec + implementation + tests — in seconds.
4. The new tool is **hot-loaded** into the registry. No restart.
5. ARIA continues execution with the freshly created tool.
6. MCP clients see the new tool appear automatically through polling-driven refresh.

```
"What's the weather in Tokyo and convert it to Fahrenheit?"

  ARIA: I need weather_lookup (exists) and temp_converter (missing)
    -> Vibe synthesizes temp_converter in ~15 seconds
    -> Tool registered, tested, loaded
    -> ARIA executes the full plan
    -> Answer delivered
```

---

## Architecture

```
+------------------------------------------------------------------+
|                            Clients                               |
|                                                                  |
|   Browser (Next.js UI)     Claude Desktop / ChatGPT / Cursor     |
|         |                              |                         |
|         | Clerk JWT                    | OAuth 2.1 + PKCE        |
|         v                              v                         |
|   +-------------+                +--------------+                |
|   | chat_backend|                |  mcp_server  |                |
|   |   :8765     |                |    :8768     |                |
|   |  Planning   |                |  MCP bridge  |                |
|   |  (Mistral)  |                |  + Auth AS   |                |
|   +------+------+                +-------+------+                |
|          |                               |                       |
|          |  tool execution (HTTP)        |                       |
|          v                               v                       |
|   +--------------------------------------------+                 |
|   |            registry_api  :8766             |                 |
|   |   Tool CRUD, search, execute, auth         |                 |
|   +---------+--------------------------+-------+                 |
|             |                          |                         |
|             v                          v                         |
|   +------------------+      +------------------+                 |
|   |  tool_executor   |      | synthesis_service|                 |
|   |      :8767       |      |      :8002       |                 |
|   |  Sandboxed run   |      |  OpenCode + NIM  |                 |
|   +------------------+      +--------+---------+                 |
|                                      | webhook on completion     |
|                                      v                           |
|                              registry/tools/ on disk             |
|                                                                  |
|   PostgreSQL :5432 . Redis :6379 (cache, rate-limit)             |
+------------------------------------------------------------------+
```

---

## Services

| Service | Port | Language | Responsibility |
|---------|------|----------|----------------|
| `registry_api` | 8766 | Python | Tool registry: CRUD, search, execution, Clerk auth |
| `chat_backend` | 8765 | Python | Mistral planner → task DAG → AG2 multi-agent executor → SSE stream |
| `synthesis_service` | 8002 | Python | Spawns OpenCode + NIM to generate `spec.yaml` + `impl.py`, calls back to registry |
| `tool_executor` | 8767 | Python | Stub for sandboxed tool execution (gVisor migration planned) |
| `mcp_server` | 8768 | Python | MCP JSON-RPC bridge + OAuth 2.1/PKCE Authorization Server (Clerk for identity) |
| `registry_ui` | 3001 | TypeScript | Next.js 16 + React 19 frontend (chat, catalog, publish, settings) |
| `postgres` | 5432 | — | Tool metadata, user profiles, usage stats |
| `redis` | 6379 | — | Cache, per-user rate limiting, session state |

---

## Tool Format

Every tool is a pair of files on disk: `registry/tools/{id}/{version}/spec.yaml` + `{entrypoint}.py`.

**`spec.yaml`** — what the tool does:
```yaml
kiln_version: "1.0"

tool:
  id: com.kiln.tools.fetch_url
  name: fetch_url
  version: "1.0.0"
  description: "Fetch any public URL and return plain-text content with HTML stripped."
  author: aria

interface:
  inputs:
    - name: url
      type: string
      description: "Full URL to fetch"
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

testing:
  fixtures:
    - input:
        url: "https://en.wikipedia.org/wiki/Singapore"
      expected_output_contains: [title, content]

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

The format is framework-agnostic. Kiln's `compiler/` module translates specs to AG2, Mistral, LangChain, or Pydantic AI tool definitions on the fly, so any tool — hand-written or synthesized — works with any supported framework without modification.

---

## MCP Support

Kiln exposes every registered tool over the [Model Context Protocol](https://modelcontextprotocol.io/) via `mcp_server` on port 8768. Standard MCP clients connect and use Kiln tools directly.

- **Auth**: OAuth 2.1 with PKCE. The MCP server is itself the OAuth Authorization Server; Clerk provides user identity via redirect.
- **Dynamic client registration** (RFC 7591) is enabled — MCP clients register themselves without manual config.
- **User context**: once authenticated, the user's saved tool env vars (e.g. `NEWS_API_KEY`, `SERPER_API_KEY`) stored in Clerk `private_metadata` are injected into tool execution automatically.
- **Hot refresh**: newly synthesized tools become available to MCP clients through 30-second polling (and via the `kiln_refresh_tools` utility tool).
- **Fallback**: if `CLERK_DOMAIN` is unset, the server runs unauthenticated for local dev.

To connect **Claude Desktop** (or any MCP client), point it at `http://localhost:8768/mcp`. The browser will open Clerk's sign-in page, then hand control back to the client.

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- A [Mistral API key](https://console.mistral.ai/) for the planner
- An [NVIDIA NIM API key](https://build.nvidia.com/) for Vibe synthesis
- A [Clerk](https://dashboard.clerk.com) project (publishable + secret keys)

Local development without Docker additionally needs Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 20+, and [pnpm](https://pnpm.io/).

### 1. Clone and configure

```bash
git clone git@github.com:aryabyte21/kiln.git
cd kiln
cp .env.example .env
# Fill in .env:
#   MISTRAL_API_KEY, NVIDIA_API_KEY
#   CLERK_DOMAIN, CLERK_SECRET_KEY, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
#   KILN_INTERNAL_SECRET (generate: python -c "import secrets; print(secrets.token_hex(32))")
```

### 2. Start everything

```bash
./dev.sh
```

This builds and starts all services via Docker Compose with hot-reload enabled. Container health checks gate startup order so dependents wait for the registry.

### 3. Use it

- **Web UI**: [http://localhost:3001](http://localhost:3001) — sign in via Clerk, start chatting
- **Registry API**: [http://localhost:8766](http://localhost:8766) — direct HTTP access
- **MCP server**: `http://localhost:8768/mcp` — connect from Claude Desktop, ChatGPT, etc.

---

## Local Development (without Docker)

```bash
# Install Python deps (uv workspace across all packages)
uv sync

# Install frontend deps
cd packages/registry_ui && pnpm install && cd ../..

# Run services individually (each in its own terminal)
uv run uvicorn kiln_registry.main:app --host 0.0.0.0 --port 8766 --reload
uv run uvicorn kiln_chat_backend.main:app --host 0.0.0.0 --port 8765 --reload
uv run uvicorn kiln_synthesis.main:app --host 0.0.0.0 --port 8002
uv run python -m kiln_mcp.main streamable-http
cd packages/registry_ui && pnpm run dev
```

### Tests & lint

```bash
uv run pytest                                    # All Python tests
uv run pytest packages/mcp_server/tests/ -v      # Per-package
uv run ruff check packages/                      # Lint Python
uv run mypy packages/                            # Type check
cd packages/registry_ui && pnpm run lint         # Frontend lint (zero warnings)
cd packages/registry_ui && pnpm run build        # Frontend build
```

### Monorepo orchestration

```bash
pnpm nx run <project>:<target>   # Nx-cached tasks across the workspace
```

---

## Repository Layout

```
kiln/
├── packages/
│   ├── shared/              # kiln_shared — auth, rate-limit, config, CORS, logging
│   ├── registry_api/        # kiln_registry — tool CRUD, search, execute, Clerk auth
│   ├── chat_backend/        # kiln_chat_backend — planner, DAG executor, SSE
│   ├── synthesis_service/   # kiln_synthesis — OpenCode + NIM wrapper
│   ├── tool_executor/       # kiln_executor — sandboxed execution
│   ├── mcp_server/          # kiln_mcp — MCP bridge + OAuth 2.1 AS
│   │   └── kiln_mcp/auth/   # store, provider, Clerk callback
│   └── registry_ui/         # Next.js 16 / React 19 / Tailwind 4 / Clerk
├── registry/
│   └── tools/               # All registered tools (spec.yaml + impl.py per version)
├── docs/
│   └── superpowers/
│       ├── specs/           # Design specs for major features
│       └── plans/           # Implementation plans
├── infra/
│   ├── terraform/           # GCP infrastructure (GKE, VPC, IAM, storage)
│   └── tanka/               # Kubernetes manifests (Jsonnet)
├── docker-compose.yml       # Dev stack definition
├── docker/                  # Service Dockerfiles
├── dev.sh                   # Convenience launcher
├── pyproject.toml           # uv workspace root
├── package.json             # pnpm root + Nx config
└── .env                     # API keys (git-ignored)
```

---

## Tech Stack

| Layer | Stack |
|-------|-------|
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.0, asyncpg/aiosqlite, Alembic-ready |
| Multi-agent | PyAutoGen (AG2) |
| LLM | Mistral Large (planning), Mistral Codestral / NVIDIA NIM (synthesis) |
| Auth | Clerk (JWT for browser, API keys for CLI, OAuth 2.1 for MCP clients) |
| Frontend | Next.js 16, React 19, TypeScript 5.9, Tailwind 4, shadcn/ui, TanStack Query, Vercel AI SDK |
| MCP | Anthropic MCP SDK 1.26+, streamable HTTP transport |
| Infra (local) | Docker Compose, PostgreSQL 17, Redis 7 |
| Infra (cloud) | Terraform, GKE Standard, nginx-ingress, cert-manager (Let's Encrypt), Tanka/Jsonnet, Prometheus, Grafana, GitHub Actions CI/CD |
| Tooling | uv (Python), pnpm + Nx (monorepo), ruff, mypy, ESLint |

---

## Security Model

- **Synthesized code runs in a constrained subprocess** — dangerous imports and built-ins (shell execution, unsafe deserializers, direct socket access, GUI automation, etc.) are rejected at registration and execution time via AST validation.
- **Per-tool env vars declared explicitly** — each tool's `REQUIRED_ENV_VARS` list is surfaced to the user; keys are stored encrypted in Clerk `private_metadata` and injected at execution time.
- **OAuth 2.1 + PKCE** for MCP clients — HMAC-signed state, single-use auth codes, paired access/refresh token revocation, client-bound token lookups.
- **Service-to-service auth** via `X-Internal-Secret` header for inter-service calls inside the Docker network.
- **Rate limiting** per user/IP via slowapi + Redis.
- **CORS** strictly allowlisted by `KILN_ENV` — production refuses to start without an explicit `CORS_ORIGINS`.

---

## Live Deployment

Kiln is deployed on **GKE Standard** in Singapore (`asia-southeast1-a`).

| Service | URL |
|---------|-----|
| Registry UI | https://kiln.35.197.159.116.sslip.io |
| Registry API | https://api.35.197.159.116.sslip.io |
| Chat Backend | https://chat.35.197.159.116.sslip.io |
| MCP Server | https://mcp.35.197.159.116.sslip.io |
| Grafana | https://grafana.35.197.159.116.sslip.io |

### Grafana

- **URL**: https://grafana.35.197.159.116.sslip.io
- **Username**: `admin`
- **Password**: `kiln-admin`
- **Dashboards**: Pre-configured Prometheus datasource. Metrics include `kiln_http_requests_total` (request count by service/method/path/status) and `kiln_http_request_duration_seconds` (latency histogram).

### MCP Client Connection

Point any MCP client (Claude Desktop, Cursor, VS Code Copilot) at:
```
https://mcp.35.197.159.116.sslip.io/mcp
```

### Infrastructure

- **Cluster**: 1x e2-standard-2 (2 vCPU, 8 GB) on-demand + 0-3x e2-small spot burst pool
- **Data**: In-cluster PostgreSQL 17 (10Gi PVC) + Redis 7 (ephemeral)
- **Observability**: Self-hosted Prometheus + Grafana
- **CI/CD**: Push to `main` triggers automated test, build, and deploy via GitHub Actions + Workload Identity Federation
- **IaC**: Terraform (GCP infra) + Tanka/Jsonnet (Kubernetes manifests)
- **Cost**: ~$70/month on $380 student credits (~5 months runway)

See `docs/infrastructure-report.md` for full deployment documentation.

---

## Project Status

Kiln is a **CS5224 Cloud Computing** project at NUS (AY2025/26 Semester 2). See `docs/Final-Report.md` for the submission report and `docs/infrastructure-report.md` for deployment architecture.

## AI Declaration

AI tools were used to accelerate development and enforce code quality:

- **Code Review**: Gemini Code Assist, GitHub Copilot, and Cubic reviewed every PR, catching issues like non-atomic Redis locks, Kubernetes selector mismatches, and env var expansion bugs before they reached production.
- **Documentation**: AI assisted in drafting infrastructure reports and deployment plans, which were reviewed and corrected by the team.

All architectural decisions, deployment debugging, and cost tradeoffs were made by the team.

---

## License

Apache 2.0
