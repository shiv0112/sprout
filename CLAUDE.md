# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Sprout (formerly Babel) is a self-evolving tool registry for AI agents. Users ask something in natural language, ARIA (planning agent) decomposes it into a task graph, and if a tool is missing, Vibe (synthesis agent) generates it on the fly. No restarts needed.

## Commands

### Full stack (Docker — recommended)
```bash
./dev.sh                    # Start all services via docker-compose (requires .env)
```

### Python services (local dev)
```bash
uv sync                     # Install all Python deps
uv run pytest               # Run tests
uv run ruff check .         # Lint
uv run mypy .               # Type check

# Individual services
uv run uvicorn sprout_registry.main:app --host 0.0.0.0 --port 8766 --reload    # Registry API
uv run uvicorn sprout_chat_backend.main:app --host 0.0.0.0 --port 8765 --reload # Chat Backend
uv run python -m sprout_mcp.main streamable-http                                # MCP Server
uv run uvicorn sprout_synthesis.main:app --host 0.0.0.0 --port 8002             # Synthesis
```

### Frontend (registry_ui)
```bash
cd packages/registry_ui
npm run dev       # Next.js dev server (Turbopack)
npm run build     # Production build
npm run lint      # ESLint (--max-warnings=0)
```

### Monorepo orchestration
```bash
pnpm nx run <project>:<target>   # Nx tasks (build, lint, test — cached)
```

## Architecture

### Monorepo Layout

**Python workspace** (uv, pyproject.toml) — 6 packages:
- **shared** (`sprout_shared`) — Framework-agnostic models: `SproutToolSpec`, `SproutTool`, `@sprout_tool` decorator, auth (`require_auth`, `require_jwt_auth`), config. Zero framework imports by design.
- **registry_api** (`sprout_registry`, port 8766) — Core service. Loads tool specs from `registry/tools/` into memory, persists metadata in SQL, handles tool execution/registration/search.
- **chat_backend** (`sprout_chat_backend`, port 8765) — Planning via Mistral Large → task DAG, execution via AG2 multi-agent. SSE streaming for real-time progress.
- **synthesis_service** (`sprout_synthesis`, port 8002) — Runs Mistral Vibe CLI in Docker subprocess to generate spec.yaml + impl.py. Callbacks to registry_api on completion.
- **tool_executor** (`sprout_executor`) — Stub for future gVisor-sandboxed execution.
- **mcp_server** (`sprout_mcp`, port 8768) — MCP bridge: exposes Sprout tools as MCP tools, supports streamable-http and stdio transports.

**Node workspace** (pnpm, nx) — 1 package:
- **registry_ui** (port 3000) — Next.js 16 (App Router, React 19), Tailwind 4, Clerk auth, Vercel AI SDK, TanStack Query.

### Key Architectural Patterns

- **HTTP-based tool proxy**: AG2 agents don't import tools directly. They call HTTP stubs that POST to registry_api. This enables hot-reload, distributed execution, and access control at the HTTP layer.
- **Spec-driven tools**: Every tool = YAML spec + Python impl in `registry/tools/{id}/{version}/`. Compilers in `sprout_registry/compiler/` translate specs to AG2, Mistral, LangChain, or Pydantic AI formats.
- **Topological DAG execution**: `SproutGraphFlow` uses Kahn's algorithm on the task graph from the planner.
- **Dual auth**: Clerk JWT (browser) + API keys (CLI/MCP). `require_auth` tries both; `require_jwt_auth` is Clerk-only.
- **Dual database**: SQLAlchemy async with asyncpg (PostgreSQL) or aiosqlite (SQLite fallback).
- **Queue-based SSE**: Execution events pushed to thread-safe queues, drained by SSE response generators.

### Service Dependencies (docker-compose)

```
postgres, redis → registry_api → chat_backend, mcp_server, synthesis_service
                  registry_api + chat_backend → registry_ui
```

### Tool Spec Format

Tools in `registry/tools/` follow: `{id}/{version}/spec.yaml` + `{entrypoint}.py`. The spec defines interface (inputs/outputs), implementation (runtime, entrypoint, timeout), compiler targets, and test fixtures.

## Tech Stack

| Layer | Stack |
|-------|-------|
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.0, asyncpg/aiosqlite, Alembic |
| LLM | Mistral Large (planning), Mistral Vibe CLI (synthesis) |
| Multi-agent | PyAutoGen (AG2) |
| Frontend | Next.js 16, React 19, TypeScript 5.9, Tailwind 4, Clerk, AI SDK |
| Infra | Docker Compose, PostgreSQL 17, Redis 7, uv, pnpm, Nx |

## Style

- Python: ruff (line-length 120, rules: E/F/I/W/UP/B/SIM, ignores E501/B008)
- TypeScript: ESLint 9 with zero warnings policy
- Next.js 16 has breaking changes vs training data — read `node_modules/next/dist/docs/` before writing Next.js code