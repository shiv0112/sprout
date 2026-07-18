# Sprout Rebrand & Restructure Design Spec

**Date:** 2026-03-27
**Phase:** 1 of N — Rebrand + Restructure (local-first, no cloud deployment)
**Status:** Approved

## Context

Sprout is a self-evolving tool registry for autonomous AI agents. The existing codebase (formerly "Babel") is a working local prototype with a monolithic FastAPI server, Mistral-powered planning (ARIA), Mistral Vibe CLI synthesis, AG2/AutoGen execution, React chat UI, and 40+ registered tools.

This spec covers rebranding Babel → Sprout and restructuring the codebase into a multi-service Nx monorepo aligned with the manifesto's Cloud Run microservice architecture, while keeping everything runnable locally.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Auth provider | Clerk (later phase) | Native RBAC, API keys, org support, cloud-agnostic |
| Planning LLM | Mistral Large (unchanged) | Working, no Claude API available |
| Synthesis LLM | Mistral Vibe CLI (unchanged) | Working, swap to Claude API later |
| Monorepo tooling | Nx | Manifesto requirement, manages multi-service builds |
| Python package manager | uv | Fast, modern, workspace support |
| Tool namespace | `com.sprout.tools.*` | Full rebrand from `com.aria.tools.*` |
| Frontend | Rename to `chat_ui/`, rebrand to "Sprout Chat" | Registry UI deferred |
| Database | SQLite (unchanged) | PostgreSQL migration is a later phase |

## Target Directory Structure

```
sprout/
├── nx.json
├── pyproject.toml              # uv workspace root
├── uv.lock
├── packages/
│   ├── shared/
│   │   ├── pyproject.toml      # sprout-shared
│   │   ├── project.json
│   │   └── sprout_shared/
│   │       ├── __init__.py
│   │       ├── spec.py         # SproutToolSpec
│   │       ├── models.py       # Shared Pydantic models
│   │       └── schema/
│   │           └── sprout.schema.json
│   ├── chat_backend/
│   │   ├── pyproject.toml      # sprout-chat-backend
│   │   ├── project.json
│   │   └── sprout_chat_backend/
│   │       ├── __init__.py
│   │       ├── main.py         # FastAPI app (planning + execution + SSE)
│   │       ├── planner.py      # SproutPlanner
│   │       └── graph_flow.py   # SproutGraphFlow
│   ├── registry_api/
│   │   ├── pyproject.toml      # sprout-registry-api
│   │   ├── project.json
│   │   └── sprout_registry/
│   │       ├── __init__.py
│   │       ├── main.py         # FastAPI app (tool CRUD + registry + execution)
│   │       ├── registry.py     # SproutRegistry
│   │       ├── sqlite_registry.py
│   │       ├── loader.py       # SproutLoader
│   │       ├── runtime.py      # SproutRuntime
│   │       └── compiler/
│   │           ├── __init__.py
│   │           ├── base.py
│   │           ├── ag2.py
│   │           ├── langchain.py
│   │           ├── pydantic_ai.py
│   │           └── mistral.py
│   ├── tool_executor/
│   │   ├── pyproject.toml      # sprout-tool-executor (placeholder for cloud phase)
│   │   ├── project.json
│   │   └── sprout_executor/
│   │       ├── __init__.py
│   │       └── main.py         # Placeholder — execution stays in registry_api for now
│   ├── synthesis_service/
│   │   ├── pyproject.toml      # sprout-synthesis-service
│   │   ├── project.json
│   │   └── sprout_synthesis/
│   │       ├── __init__.py
│   │       ├── main.py         # FastAPI app
│   │       ├── pipeline.py
│   │       ├── prompt_builder.py
│   │       ├── vibe_runner.py
│   │       ├── callback.py
│   │       ├── models.py
│   │       ├── config.py
│   │       ├── jobs/
│   │       │   └── job_store.py  # Job tracking + SSE event queueing
│   │       └── routes/
│   │           ├── synthesize.py
│   │           ├── health.py
│   │           └── events.py
│   ├── mcp_server/
│   │   ├── pyproject.toml      # sprout-mcp-server (placeholder for MCP phase)
│   │   ├── project.json
│   │   └── sprout_mcp/
│   │       ├── __init__.py
│   │       └── main.py         # Placeholder — MCP server built in a later phase
│   └── chat_ui/
│       ├── package.json
│       ├── project.json
│       ├── vite.config.ts
│       ├── tsconfig.json
│       └── src/
│           ├── App.tsx
│           ├── App.css
│           ├── main.tsx
│           └── index.css
├── registry/tools/             # com.sprout.tools.* (renamed)
├── demo/                       # Relocated demo scripts (updated imports)
├── docker-compose.yml          # Updated service names + callback URL
└── .gitignore
```

## Rebrand Mapping

### Class & Module Renames

| Current | New |
|---|---|
| `BabelToolSpec` | `SproutToolSpec` |
| `BabelRegistry` | `SproutRegistry` |
| `BabelLoader` | `SproutLoader` |
| `BabelRuntime` | `SproutRuntime` |
| `ARIAPlanner` | `SproutPlanner` |
| `ARIAGraphFlow` | `SproutGraphFlow` |
| `babel.schema.json` | `sprout.schema.json` |
| `babel_registry.db` | `sprout_registry.db` |
| `babel_version` (in spec.yaml) | `sprout_version` |

### API Route Renames

| Current | New | Service |
|---|---|---|
| `POST /aria/start` | `POST /sprout/start` | chat_backend |
| `POST /aria/execute/{run_id}` | `POST /sprout/execute/{run_id}` | chat_backend |
| `GET /aria/stream/{run_id}` | `GET /sprout/stream/{run_id}` | chat_backend |
| `POST /vibe/synthesize` | `POST /synthesis/synthesize` | synthesis_service |
| `POST /vibe/callback` | `POST /synthesis/callback` | registry_api |
| `POST /tools/{id}/execute` | `POST /tools/{id}/execute` | registry_api (unchanged) |
| `GET /audio` | `GET /audio` | registry_api (if still needed) |

### Tool Namespace

All 40+ tools: `com.aria.tools.<name>` → `com.sprout.tools.<name>`

Affected per tool:
- `spec.yaml`: `tool.id` field
- `spec.yaml`: `babel_version: "1.0"` → `sprout_version: "1.0"`
- Directory path: `registry/tools/com.aria.tools.<name>/` → `registry/tools/com.sprout.tools.<name>/`
- Version subdirectory structure preserved: `<tool_id>/1.0.0/{spec.yaml, impl.py}`

### Frontend

- Window title: "Babel Chat" → "Sprout Chat"
- All header text, button labels, references to "ARIA" → "Sprout"
- Vite proxy config updated for multi-port backend

### Schema

- `babel.schema.json` → `sprout.schema.json`
- `babel_version` field renamed to `sprout_version` in the JSON schema definition
- Loader validation code updated to match

## server.py Split Strategy

The monolithic `babel_registry/server.py` (~34KB) splits into 2 active FastAPI applications + 1 placeholder. Tool execution stays in registry_api for now (splitting to a separate executor is premature at local dev scale).

### registry_api (port 8766)
- `GET /health`
- `GET /tools` — list all tools
- `GET /tools/{tool_id}` — get single tool
- `POST /tools/register` — register tool (multipart)
- `DELETE /tools/{tool_id}` — unregister tool
- `POST /tools/{tool_id}/execute` — execute tool (stays here, not split to executor yet)
- `POST /tools/{tool_id}/test` — run test fixtures
- `POST /synthesis/callback` — webhook from synthesis service
- `GET /audio` — serve generated audio files (if still used)

### chat_backend (port 8765)
- `POST /sprout/start` — planning phase (calls registry_api for tool list)
- `POST /sprout/execute/{run_id}` — execute task graph
- `GET /sprout/stream/{run_id}` — SSE event stream
- Internal HTTP calls to registry_api for tool lookups and execution

### tool_executor (placeholder)
- Scaffold only. In the cloud phase, `POST /tools/{id}/execute` moves from registry_api to tool_executor running in gVisor. For local dev, it stays in registry_api.

### synthesis_service (port 8002)
- `POST /synthesize` — accept synthesis request
- `GET /health` — health check
- `GET /events/{job_id}` — SSE synthesis progress
- Webhook callback to registry_api when done

### Inter-Service Communication (Local Dev)

```
chat_ui (:5173)
  ├── /sprout/*          → chat_backend (:8765)
  ├── /tools/*         → registry_api (:8766)
  ├── /synthesis/*     → synthesis_service (:8002)
  └── /audio/*         → registry_api (:8766)

chat_backend (:8765)
  ├── GET /tools       → registry_api (:8766)
  └── POST /tools/*/execute → registry_api (:8766)

synthesis_service (:8002)
  └── POST /synthesis/callback → registry_api (:8766)
```

### Vite Proxy Config (chat_ui/vite.config.ts)

```typescript
server: {
  proxy: {
    '/sprout':       'http://localhost:8765',
    '/tools':      'http://localhost:8766',
    '/synthesis':  'http://localhost:8002',
    '/audio':      'http://localhost:8766',
  }
}
```

## Nx Configuration

Each service gets a `project.json` with targets:

**Python services:** `dev` (uvicorn --reload), `lint` (ruff), `test` (pytest)
**chat_ui:** `dev` (vite), `build` (vite build), `lint` (eslint)

Root `nx.json` defines task dependencies (e.g., `chat_backend:dev` implicitly depends on `shared`).

## uv Workspace

Root `pyproject.toml` defines workspace members. Each Python package declares:
- Its own dependencies in its `pyproject.toml`
- A dependency on `sprout-shared` as a local path dependency
- `uv sync` at root installs everything

### Current Dependencies to Capture

The project currently has no `requirements.txt` or `pyproject.toml`. Key dependencies from imports:
- **shared:** pydantic, pyyaml, jsonschema
- **registry_api:** fastapi, uvicorn, httpx, python-multipart + shared deps
- **chat_backend:** fastapi, uvicorn, httpx, mistralai, ag2/autogen + shared deps
- **synthesis_service:** fastapi, uvicorn, pydantic, httpx (Vibe CLI installed separately in Docker)
- **chat_ui:** react, typescript, vite (already in package.json)

## Migration Strategy

### Ordering

1. **Scaffold Nx + uv workspace** — create root configs, package directories, `project.json` files
2. **Move shared code** — extract `spec.py`, `models.py`, schema into `packages/shared/`
3. **Move registry code** — move registry, loader, runtime, compiler into `packages/registry_api/`
4. **Split server.py** — extract registry routes → `registry_api/main.py`, planning routes → `chat_backend/main.py`
5. **Move synthesis code** — move `vibe_tool/app/` into `packages/synthesis_service/` (including `jobs/`)
6. **Move frontend** — move `aria-ui/` to `packages/chat_ui/`, update proxy config
7. **Rebrand all names** — class renames, route renames, UI text
8. **Rename tool namespace** — script to rename all 40+ tool directories and spec.yaml files
9. **Update docker-compose** — service name, callback URL (`http://host.docker.internal:8766/synthesis/callback`)
10. **Move demo + test files** — `demo_*.py` → `demo/`, `test_babel.py` → service-level tests
11. **Delete old files** — remove `babel_registry/`, `aria/`, `vibe_tool/`, `aria-ui/`, `run_server.py`, `test_babel.py`
12. **Verify end-to-end** — run all services, test chat flow

### SQLite Migration

The existing `babel_registry.db` contains tool metadata with `com.aria.tools.*` IDs. Options:
- **Delete and rebuild** — `SproutLoader` rescans `registry/tools/` on startup, so deleting the DB and restarting repopulates it from disk. This is the simplest approach since the DB is just a cache of what's on disk.
- The old `babel_registry.db` file is deleted as part of cleanup.

### Disposed Files

| File | Disposition |
|---|---|
| `run_server.py` | Replaced by `nx run chat_backend:dev` + `nx run registry_api:dev` |
| `test_babel.py` | Split into per-service test files, renamed references |
| `demo_*.py` (6 files) | Moved to `demo/`, imports updated to new package paths |
| `babel_registry.db` | Deleted — regenerated on startup from disk tools |

## What Stays Unchanged

- All tool implementations (`registry/tools/*/impl.py`) — logic untouched
- Mistral Large for planning
- Mistral Vibe CLI for synthesis
- AG2/AutoGen for graph execution
- React state machine, DAG visualization, SSE streaming
- Docker for synthesis service
- SQLite as persistence mechanism (data regenerated)
- `.env` configuration

## Out of Scope

- **MCP Server** — `packages/mcp_server/` is a placeholder. Built in a later phase.
- **Tool Executor separation** — `packages/tool_executor/` is a placeholder. Execution stays in registry_api. Extracted when deploying to Cloud Run with gVisor.
- **Clerk auth integration** — Later phase.
- **PostgreSQL migration** — Later phase.
- **Registry UI** — Later phase.
- **Cloud deployment (GCP/Cloud Run)** — Later phase.

## Success Criteria

1. `nx run-many --target=dev` starts all active services (registry_api, chat_backend, synthesis_service, chat_ui)
2. Submitting a query in Sprout Chat UI produces a plan, executes it, and streams results
3. Tool synthesis works end-to-end (request → generate → callback → register → available)
4. All 40+ tools accessible under `com.sprout.tools.*` namespace
5. No references to "Babel" or "ARIA" remain in source code, UI text, comments, docstrings, or test files (git history excluded)
6. `uv sync` at root installs all Python dependencies across all packages
7. Each service can also be run individually via `nx run <service>:dev`
