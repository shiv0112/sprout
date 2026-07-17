# Kiln Monorepo Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the existing Babel prototype into a production-grade, cloud-native SaaS monorepo ("Kiln") — a self-evolving tool registry for autonomous AI agents with MCP protocol support, 5 independently deployable microservices, Firebase Auth, PostgreSQL, and two React frontends.

**Architecture:** Polyglot monorepo with uv workspaces (Python 3.13) and pnpm workspaces (TypeScript). Five FastAPI microservices (Chat Backend, Registry API, MCP Server, Tool Executor, Synthesis Service) deployed to Google Cloud Run. Two React 19 SPAs (Chat UI, Registry UI) on Firebase Hosting. PostgreSQL 16 via Cloud SQL for persistence, GCS for tool artifacts, Cloud Pub/Sub for async synthesis messaging, Firebase Auth for shared identity. The MCP Server exposes the entire tool library via JSON-RPC to any MCP-compatible AI client (Claude Desktop, Cursor, VS Code Copilot, Windsurf).

**Tech Stack:**
- **Python:** 3.13, uv, FastAPI 0.115+, Pydantic v2, SQLAlchemy 2.0 (async), Alembic, pytest, Ruff
- **TypeScript:** 5.9, React 19, Vite 7, Tailwind CSS v4, shadcn/ui, TanStack Query v5, TanStack Router v1, Zustand, Vitest, Biome
- **Infrastructure:** PostgreSQL 16, Cloud SQL, GCS, Cloud Pub/Sub, Firebase Auth, Cloud Run (gVisor), Terraform, Docker Compose, GitHub Actions
- **Observability:** OpenTelemetry, Cloud Trace, Cloud Logging
- **Protocol:** MCP (Model Context Protocol) v2025-11-25 via JSON-RPC over SSE

**Reference:** `docs/Preliminary-1.pdf` — CS5224 Cloud Computing Preliminary Report

---

## Target Monorepo Structure

```
kiln/
├── packages/
│   └── core/                         # kiln-core Python library
│       ├── pyproject.toml
│       ├── src/kiln_core/
│       │   ├── __init__.py
│       │   ├── spec.py               # BabelToolSpec → KilnToolSpec
│       │   ├── registry.py           # Registry interface (abstract)
│       │   ├── pg_registry.py        # PostgreSQL registry (used by registry-api service)
│       │   ├── loader.py             # Tool loader (spec.yaml + impl.py)
│       │   ├── runtime.py            # Framework compilation runtime
│       │   ├── schema.json           # Kiln tool JSON Schema
│       │   └── compiler/
│       │       ├── __init__.py
│       │       ├── base.py
│       │       ├── ag2.py
│       │       ├── langchain.py
│       │       ├── mistral.py
│       │       └── pydantic_ai.py
│       └── tests/
│           ├── conftest.py
│           ├── test_spec.py
│           ├── test_registry.py
│           ├── test_loader.py
│           ├── test_runtime.py
│           └── test_compilers.py
│
├── services/
│   ├── registry-api/                 # Tool CRUD + search + RBAC + semver
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── alembic/                  # DB migrations
│   │   ├── src/registry_api/
│   │   │   ├── __init__.py
│   │   │   ├── main.py              # FastAPI app factory
│   │   │   ├── config.py            # Pydantic Settings
│   │   │   ├── deps.py              # Dependency injection
│   │   │   ├── auth.py              # Firebase JWT verification
│   │   │   ├── db.py                # SQLAlchemy async engine
│   │   │   ├── models.py            # SQLAlchemy ORM models
│   │   │   ├── schemas.py           # Pydantic request/response
│   │   │   └── routes/
│   │   │       ├── tools.py         # CRUD, search, versions
│   │   │       ├── namespaces.py    # Namespace management
│   │   │       └── health.py
│   │   └── tests/
│   │
│   ├── chat-backend/                 # Planning agent + graph executor + SSE
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── alembic/
│   │   ├── src/chat_backend/
│   │   │   ├── __init__.py
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── deps.py
│   │   │   ├── auth.py
│   │   │   ├── db.py
│   │   │   ├── models.py            # Sessions, messages, key vault
│   │   │   ├── planner.py           # LLM Planning Agent (Claude)
│   │   │   ├── graph_executor.py    # DAG executor (Kahn's algorithm)
│   │   │   ├── key_vault.py         # Encrypted API key storage
│   │   │   └── routes/
│   │   │       ├── chat.py          # POST /chat, SSE streaming
│   │   │       ├── sessions.py      # Session management
│   │   │       └── health.py
│   │   └── tests/
│   │
│   ├── mcp-server/                   # MCP JSON-RPC ↔ Registry REST adapter
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── src/mcp_server/
│   │   │   ├── __init__.py
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── auth.py              # OAuth 2.1 + PKCE
│   │   │   ├── protocol.py          # MCP JSON-RPC message handling
│   │   │   ├── handlers.py          # tools/list, tools/call, notifications
│   │   │   └── registry_client.py   # HTTP client to Registry API
│   │   └── tests/
│   │
│   ├── tool-executor/                # gVisor-sandboxed execution
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── src/tool_executor/
│   │   │   ├── __init__.py
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── sandbox.py           # gVisor execution environment
│   │   │   ├── installer.py         # pip install from requirements.txt
│   │   │   └── routes/
│   │   │       ├── execute.py       # POST /execute
│   │   │       └── health.py
│   │   └── tests/
│   │
│   └── synthesis/                    # Claude API code gen + Pub/Sub
│       ├── pyproject.toml
│       ├── Dockerfile
│       ├── src/synthesis/
│       │   ├── __init__.py
│       │   ├── main.py
│       │   ├── config.py
│       │   ├── consumer.py          # Pub/Sub subscription handler
│       │   ├── generator.py         # Claude API tool generation
│       │   ├── validator.py         # Schema validation + fixture testing
│       │   └── publisher.py         # Webhook callback to Registry API
│       └── tests/
│
├── apps/
│   ├── chat-ui/                      # React Chat SPA
│   │   ├── package.json
│   │   ├── vite.config.ts
│   │   ├── tailwind.config.ts
│   │   ├── tsconfig.json
│   │   ├── index.html
│   │   └── src/
│   │       ├── main.tsx
│   │       ├── app.tsx
│   │       ├── router.tsx
│   │       ├── lib/
│   │       │   ├── api.ts            # Chat backend API client
│   │       │   ├── auth.ts           # Firebase Auth hooks
│   │       │   └── sse.ts            # SSE stream hook
│   │       ├── stores/
│   │       │   └── chat-store.ts     # Zustand chat state
│   │       ├── components/
│   │       │   ├── ui/               # shadcn/ui components
│   │       │   ├── chat-input.tsx
│   │       │   ├── message-list.tsx
│   │       │   ├── dag-view.tsx      # Task graph visualization
│   │       │   ├── node-card.tsx
│   │       │   ├── log-panel.tsx
│   │       │   └── env-config.tsx    # API key configuration
│   │       └── routes/
│   │           ├── index.tsx         # Chat page
│   │           └── login.tsx
│   │
│   └── registry-ui/                  # React Registry SPA
│       ├── package.json
│       ├── vite.config.ts
│       ├── tailwind.config.ts
│       ├── tsconfig.json
│       ├── index.html
│       └── src/
│           ├── main.tsx
│           ├── app.tsx
│           ├── router.tsx
│           ├── lib/
│           │   ├── api.ts            # Registry API client
│           │   └── auth.ts           # Firebase Auth hooks
│           ├── stores/
│           │   └── registry-store.ts
│           ├── components/
│           │   ├── ui/               # shadcn/ui components
│           │   ├── tool-card.tsx
│           │   ├── tool-detail.tsx
│           │   ├── search-bar.tsx
│           │   ├── publish-form.tsx
│           │   └── version-history.tsx
│           └── routes/
│               ├── index.tsx         # Tool browser
│               ├── tool.$id.tsx      # Tool detail page
│               ├── publish.tsx       # Publish workflow
│               └── login.tsx
│
├── infra/
│   ├── terraform/
│   │   ├── main.tf                   # Provider + project config
│   │   ├── cloud-run.tf              # 5 Cloud Run services
│   │   ├── cloud-sql.tf              # 2 PostgreSQL instances
│   │   ├── storage.tf                # GCS bucket
│   │   ├── pubsub.tf                 # Topics + subscriptions
│   │   ├── firebase.tf               # Auth + Hosting
│   │   ├── iam.tf                    # Service accounts + roles
│   │   ├── secrets.tf                # Secret Manager
│   │   ├── networking.tf             # VPC + Serverless Connector
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   └── environments/
│   │       ├── dev.tfvars
│   │       └── prod.tfvars
│   └── docker/
│       └── base-python.Dockerfile    # Shared Python base image
│
├── tools/                            # Tool registry data (migrated from registry/)
│   └── com.kiln.tools.*/
│       └── 1.0.0/
│           ├── spec.yaml
│           ├── impl.py
│           └── requirements.txt
│
├── docs/
│   ├── Preliminary-1.pdf
│   └── superpowers/plans/
│
├── docker-compose.yml                # Full local dev stack
├── pyproject.toml                    # uv workspace root
├── pnpm-workspace.yaml
├── package.json                      # Root scripts
├── Taskfile.yml                      # Cross-language task runner
├── .github/
│   └── workflows/
│       ├── ci.yml                    # Test + lint on PR
│       └── deploy.yml                # Deploy to Cloud Run on merge
├── .gitignore
├── LICENSE
└── README.md
```

---

## Chunk 1: Monorepo Foundation + Core Library Migration

This chunk sets up the monorepo scaffolding and migrates the existing Babel core into a proper `kiln-core` Python package. By the end, `kiln-core` is installable, tested, and the existing tool registry works with the new package.

### Task 1: Initialize monorepo root with uv + pnpm workspaces

**Files:**
- Create: `pyproject.toml` (root workspace)
- Create: `pnpm-workspace.yaml`
- Create: `package.json` (root)
- Create: `Taskfile.yml`
- Create: `.gitignore` (replace existing)
- Create: `.python-version`

- [ ] **Step 1: Create root pyproject.toml for uv workspace**

```toml
# pyproject.toml
[project]
name = "kiln"
version = "0.1.0"
description = "A self-evolving tool registry for autonomous AI agents"
requires-python = ">=3.13"

[tool.uv.workspace]
members = [
    "packages/*",
    "services/*",
]

[tool.ruff]
target-version = "py313"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "A", "SIM", "TCH"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create pnpm workspace config**

```yaml
# pnpm-workspace.yaml
packages:
  - "apps/*"
```

- [ ] **Step 3: Create root package.json**

```json
{
  "name": "kiln",
  "private": true,
  "scripts": {
    "dev:chat": "pnpm --filter chat-ui dev",
    "dev:registry": "pnpm --filter registry-ui dev",
    "build": "pnpm -r build",
    "lint:ts": "pnpm -r lint",
    "test:ts": "pnpm -r test"
  },
  "engines": {
    "node": ">=22",
    "pnpm": ">=10"
  }
}
```

- [ ] **Step 4: Create Taskfile.yml for cross-language orchestration**

```yaml
# Taskfile.yml
version: "3"

tasks:
  install:
    desc: Install all dependencies
    cmds:
      - uv sync --all-packages
      - pnpm install

  dev:
    desc: Start all services for local development
    deps: [dev:services, dev:chat-ui, dev:registry-ui]

  dev:services:
    desc: Start all backend services (use docker compose for full stack)
    cmds:
      - docker compose up -d

  dev:chat-ui:
    dir: apps/chat-ui
    cmds: [pnpm dev]

  dev:registry-ui:
    dir: apps/registry-ui
    cmds: [pnpm dev]

  test:
    desc: Run all tests
    cmds:
      - uv run pytest
      - pnpm -r test

  test:py:
    desc: Run Python tests only
    cmds: [uv run pytest -v]

  test:ts:
    desc: Run TypeScript tests only
    cmds: [pnpm -r test]

  lint:
    desc: Lint everything
    cmds:
      - uv run ruff check .
      - pnpm -r lint

  fmt:
    desc: Format everything
    cmds:
      - uv run ruff format .
      - pnpm -r format

  db:migrate:
    desc: Run database migrations
    cmds:
      - uv run --package registry-api alembic upgrade head
      - uv run --package chat-backend alembic upgrade head

  docker:build:
    desc: Build all Docker images
    cmds:
      - docker compose build
```

- [ ] **Step 5: Create comprehensive .gitignore**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
.venv/
venv/
.uv/

# Node
node_modules/
.pnpm-store/

# Environment
.env
.env.local
.env.*.local

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Build
*.egg
build/

# Database
*.db
*.sqlite3

# Logs
*.log
logs/

# Docker
docker-compose.override.yml

# Terraform
.terraform/
*.tfstate
*.tfstate.backup
*.tfvars.local

# Test
.coverage
htmlcov/
.pytest_cache/
```

- [ ] **Step 6: Create .python-version**

```
3.13
```

- [ ] **Step 7: Verify setup**

Run: `cd /Users/pinetortoise/Desktop/kiln && cat pyproject.toml && cat pnpm-workspace.yaml`

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml pnpm-workspace.yaml package.json Taskfile.yml .gitignore .python-version
git commit -m "feat: initialize monorepo with uv + pnpm workspaces"
```

---

### Task 2: Create kiln-core Python package (migrate babel_registry core)

**Files:**
- Create: `packages/core/pyproject.toml`
- Create: `packages/core/src/kiln_core/__init__.py`
- Create: `packages/core/src/kiln_core/spec.py` (from `babel_registry/spec.py`)
- Create: `packages/core/src/kiln_core/registry.py` (abstract interface)
- Create: `packages/core/src/kiln_core/loader.py` (from `babel_registry/loader.py`)
- Create: `packages/core/src/kiln_core/runtime.py` (from `babel_registry/runtime.py`)
- Create: `packages/core/src/kiln_core/schema.json` (from `babel_registry/spec/babel.schema.json`)
- Create: `packages/core/src/kiln_core/compiler/__init__.py`
- Create: `packages/core/src/kiln_core/compiler/base.py`
- Create: `packages/core/src/kiln_core/compiler/ag2.py`
- Create: `packages/core/src/kiln_core/compiler/langchain.py`
- Create: `packages/core/src/kiln_core/compiler/mistral.py`
- Create: `packages/core/src/kiln_core/compiler/pydantic_ai.py`

- [ ] **Step 1: Create kiln-core pyproject.toml**

```toml
# packages/core/pyproject.toml
[project]
name = "kiln-core"
version = "0.1.0"
description = "Core library for Kiln: tool specs, registry interface, framework compilers"
requires-python = ">=3.13"
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "jsonschema>=4.20",
]

[project.optional-dependencies]
ag2 = ["ag2>=0.6"]
langchain = ["langchain-core>=0.3"]
mistral = ["mistralai>=1.0"]
pydantic-ai = ["pydantic-ai>=0.1"]
all = ["kiln-core[ag2,langchain,mistral,pydantic-ai]"]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/kiln_core"]
```

- [ ] **Step 2: Migrate spec.py — rename Babel→Kiln, modernize with Pydantic v2**

Port `babel_registry/spec.py` to `packages/core/src/kiln_core/spec.py`:
- Rename `BabelToolSpec` → `KilnToolSpec`, `BabelTool` → `KilnTool`
- Convert dataclasses to Pydantic BaseModel for validation
- Keep `@kiln_tool` decorator (renamed from `@babel_tool`)
- Keep `ToolParam`, `ToolReturn`
- Add `ToolVersion` model with semver support

Key changes:
```python
from pydantic import BaseModel, Field

class ToolParam(BaseModel):
    name: str
    type: str  # "str"|"int"|"float"|"bool"|"list"|"dict"|"any"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: list[str] | None = None

class KilnToolSpec(BaseModel):
    id: str
    name: str
    description: str
    params: list[ToolParam] = Field(default_factory=list)
    returns: ToolReturn = Field(default_factory=ToolReturn)
    version: str = "1.0.0"
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    category: str = "general"
    namespace: str = ""  # NEW: for RBAC scoping
```

- [ ] **Step 3: Create abstract registry interface**

`packages/core/src/kiln_core/registry.py`:
```python
from abc import ABC, abstractmethod
from .spec import KilnTool

class ToolRegistry(ABC):
    @abstractmethod
    def register(self, tool: KilnTool) -> None: ...
    @abstractmethod
    def unregister(self, tool_id: str) -> None: ...
    @abstractmethod
    def get(self, tool_id: str) -> KilnTool | None: ...
    @abstractmethod
    def search(self, query: str) -> list[KilnTool]: ...
    @abstractmethod
    def list_all(self) -> list[KilnTool]: ...
    @abstractmethod
    def has(self, tool_id: str) -> bool: ...

class InMemoryRegistry(ToolRegistry):
    """Simple in-memory implementation for testing and local dev."""
    # Port from babel_registry/registry.py
```

- [ ] **Step 4: Migrate loader.py — update imports, rename classes**

Port `babel_registry/loader.py` → `packages/core/src/kiln_core/loader.py`:
- Update all `Babel*` references to `Kiln*`
- Update schema path to use `importlib.resources`
- Keep spec.yaml validation, test fixture execution, and tool loading logic

- [ ] **Step 5: Migrate runtime.py and all compiler adapters**

Port `babel_registry/runtime.py` → `packages/core/src/kiln_core/runtime.py`
Port `babel_registry/compiler/*.py` → `packages/core/src/kiln_core/compiler/*.py`
- Update imports and class names
- Keep adapter pattern and lazy loading

- [ ] **Step 5b: Create PostgreSQL registry implementation (used by registry-api service)**

`packages/core/src/kiln_core/pg_registry.py`:
```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from .spec import KilnTool, KilnToolSpec
from .registry import ToolRegistry

class PostgresRegistry(ToolRegistry):
    """PostgreSQL-backed registry using SQLAlchemy async sessions.
    Used by the registry-api service. Requires a Tool ORM model
    (defined in registry-api/models.py) passed at init.
    """
    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def register(self, tool: KilnTool) -> None: ...
    async def get(self, tool_id: str) -> KilnTool | None: ...
    async def search(self, query: str) -> list[KilnTool]: ...
    async def list_all(self) -> list[KilnTool]: ...
    # Full implementation delegated to Task 8 when wiring with ORM models
```

- [ ] **Step 6: Copy babel.schema.json**

```bash
cp babel_registry/spec/babel.schema.json packages/core/src/kiln_core/schema.json
```

- [ ] **Step 7: Create __init__.py with public API**

```python
# packages/core/src/kiln_core/__init__.py
from .spec import KilnTool, KilnToolSpec, ToolParam, ToolReturn, kiln_tool
from .registry import ToolRegistry, InMemoryRegistry
from .loader import KilnLoader
from .runtime import KilnRuntime

__all__ = [
    "KilnTool", "KilnToolSpec", "ToolParam", "ToolReturn", "kiln_tool",
    "ToolRegistry", "InMemoryRegistry",
    "KilnLoader", "KilnRuntime",
]
```

- [ ] **Step 8: Write tests for kiln-core**

Create `packages/core/tests/test_spec.py`:
- Port relevant tests from `test_babel.py`
- Test KilnToolSpec validation, ToolParam types, @kiln_tool decorator
- Test InMemoryRegistry CRUD operations

Create `packages/core/tests/test_loader.py`:
- Test loading a tool from spec.yaml + impl.py
- Test schema validation failures
- Test fixture execution

Create `packages/core/tests/test_runtime.py`:
- Test compilation to each target framework
- Test cache invalidation

- [ ] **Step 9: Run tests**

```bash
cd packages/core && uv run pytest -v
```

- [ ] **Step 10: Commit**

```bash
git add packages/core/
git commit -m "feat: create kiln-core package — migrate babel_registry core with Pydantic v2"
```

---

### Task 3: Migrate tool registry data

**Files:**
- Move: `registry/tools/` → `tools/`
- Update: tool namespace from `com.aria.tools.*` → `com.kiln.tools.*`

- [ ] **Step 1: Move tools directory to monorepo root**

```bash
mv registry/tools tools
```

- [ ] **Step 2: Rename tool namespaces in all spec.yaml files**

Replace `com.aria.tools.` with `com.kiln.tools.` in every spec.yaml and directory name.

- [ ] **Step 3: Rename tool directories**

```bash
cd tools && for dir in com.aria.tools.*/; do
  new_name="${dir/com.aria.tools./com.kiln.tools.}"
  mv "$dir" "$new_name"
done
```

- [ ] **Step 4: Verify tool loading with kiln-core**

```python
from kiln_core import KilnLoader
loader = KilnLoader(auto_register=False)
tools = loader.load_all("tools/")
assert len(tools) > 40
```

- [ ] **Step 5: Commit**

```bash
git add tools/
git rm -r registry/
git commit -m "feat: migrate tool registry — rename namespace to com.kiln.tools"
```

---

### Task 4: Set up Docker Compose for local development

**Files:**
- Create: `docker-compose.yml` (replace existing)

- [ ] **Step 1: Write docker-compose.yml with PostgreSQL, Redis, and placeholder services**

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: kiln
      POSTGRES_PASSWORD: kiln_dev
      POSTGRES_DB: kiln_registry
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./infra/docker/init-db.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U kiln"]
      interval: 5s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  # Pub/Sub emulator for local dev
  pubsub-emulator:
    image: gcr.io/google.com/cloudsdktool/google-cloud-cli:latest
    command: >
      gcloud beta emulators pubsub start
      --host-port=0.0.0.0:8085
      --project=kiln-local
    ports:
      - "8085:8085"

volumes:
  pgdata:
```

- [ ] **Step 2: Create init-db.sql for dual-database setup**

```sql
-- infra/docker/init-db.sql
-- Create separate databases for registry and chat (matching PDF architecture)
CREATE DATABASE kiln_chat;
GRANT ALL PRIVILEGES ON DATABASE kiln_registry TO kiln;
GRANT ALL PRIVILEGES ON DATABASE kiln_chat TO kiln;

-- Enable required extensions
\c kiln_registry
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- For fuzzy text search

\c kiln_chat
CREATE EXTENSION IF NOT EXISTS pgcrypto; -- For key vault encryption
```

- [ ] **Step 3: Verify docker compose starts**

```bash
docker compose up -d postgres redis
docker compose ps
```
Expected: Both containers running and healthy.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml infra/
git commit -m "feat: add Docker Compose for local dev — PostgreSQL 16, Redis, Pub/Sub emulator"
```

---

### Task 5: Clean up legacy files

**Files:**
- Remove: `babel_registry/` (migrated to packages/core)
- Remove: `aria/` (will be rebuilt in chat-backend service)
- Remove: `vibe_tool/` (will be rebuilt in synthesis service)
- Remove: `aria-ui/` (will be rebuilt in apps/chat-ui)
- Remove: `babel_registry.db`
- Remove: `run_server.py`
- Remove: `demo_*.py`
- Remove: `test_babel.py` (ported to packages/core/tests)
- Keep: `README.md`, `LICENSE`, `docs/`

- [ ] **Step 1: Archive legacy code to a branch before deletion**

```bash
git checkout -b archive/babel-prototype
git checkout main
```

- [ ] **Step 2: Remove migrated files**

```bash
git rm -r babel_registry/ aria/ vibe_tool/ aria-ui/
git rm babel_registry.db run_server.py test_babel.py
git rm demo_ag2.py demo_aria.py demo_loader.py demo_mistral.py demo_pydantic_ai.py demo_register_tool.py
```

- [ ] **Step 3: Update README.md**

Replace with new Kiln monorepo README covering the new architecture, tech stack, setup instructions.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove legacy Babel prototype — code archived on archive/babel-prototype branch"
```

---

### Task 5b: Create .env.example and shared test fixtures

**Files:**
- Create: `.env.example`
- Create: `packages/core/tests/conftest.py`

- [ ] **Step 1: Create .env.example with all required environment variables**

```bash
# .env.example — Copy to .env and fill in values
# Firebase
FIREBASE_PROJECT_ID=kiln-dev

# Anthropic (for Synthesis + Chat Backend)
ANTHROPIC_API_KEY=sk-ant-...

# Registry API
KILN_REGISTRY_DATABASE_URL=postgresql+asyncpg://kiln:kiln_dev@localhost:5432/kiln_registry
KILN_REGISTRY_GCS_BUCKET=kiln-tool-artifacts
KILN_REGISTRY_TOOL_EXECUTOR_URL=http://localhost:8400

# Chat Backend
KILN_CHAT_DATABASE_URL=postgresql+asyncpg://kiln:kiln_dev@localhost:5432/kiln_chat
KILN_CHAT_REGISTRY_API_URL=http://localhost:8100
KILN_CHAT_TOOL_EXECUTOR_URL=http://localhost:8400
KILN_CHAT_VAULT_ENCRYPTION_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Synthesis
KILN_SYNTHESIS_GCP_PROJECT_ID=kiln-dev
KILN_SYNTHESIS_REGISTRY_API_URL=http://localhost:8100

# MCP Server
KILN_MCP_REGISTRY_API_URL=http://localhost:8100
```

- [ ] **Step 2: Create kiln-core test conftest.py**

```python
# packages/core/tests/conftest.py
import pytest
from kiln_core import InMemoryRegistry, KilnTool, KilnToolSpec, ToolParam, ToolReturn

@pytest.fixture
def registry():
    return InMemoryRegistry()

@pytest.fixture
def sample_spec():
    return KilnToolSpec(
        id="com.kiln.test.hello",
        name="hello",
        description="Says hello",
        params=[ToolParam(name="name", type="str", description="Name to greet")],
        returns=ToolReturn(type="dict"),
    )

@pytest.fixture
def sample_tool(sample_spec):
    def hello(name: str = "World") -> dict:
        return {"greeting": f"Hello, {name}!"}
    return KilnTool(spec=sample_spec, fn=hello)
```

- [ ] **Step 3: Commit**

```bash
git add .env.example packages/core/tests/conftest.py
git commit -m "chore: add .env.example + shared test fixtures"
```

---

## Chunk 2: Registry API Service

The first microservice — handles tool CRUD, full-text search (PostgreSQL GIN), namespace RBAC, semantic versioning, and delegates execution to Tool Executor. This is the backbone of the platform.

### Task 6: Scaffold Registry API service

**Files:**
- Create: `services/registry-api/pyproject.toml`
- Create: `services/registry-api/src/registry_api/__init__.py`
- Create: `services/registry-api/src/registry_api/main.py`
- Create: `services/registry-api/src/registry_api/config.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
# services/registry-api/pyproject.toml
[project]
name = "registry-api"
version = "0.1.0"
description = "Kiln Registry API — tool CRUD, search, RBAC, semver"
requires-python = ">=3.13"
dependencies = [
    "kiln-core",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "google-cloud-storage>=2.18",
    "firebase-admin>=6.6",
    "pydantic-settings>=2.6",
    "httpx>=0.28",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "httpx>=0.28",
    "testcontainers[postgres]>=4.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/registry_api"]
```

- [ ] **Step 2: Create config.py with Pydantic Settings**

```python
# services/registry-api/src/registry_api/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://kiln:kiln_dev@localhost:5432/kiln_registry"
    gcs_bucket: str = "kiln-tool-artifacts"
    firebase_project_id: str = "kiln-dev"
    tool_executor_url: str = "http://localhost:8400"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]
    log_level: str = "INFO"

    model_config = {"env_prefix": "KILN_REGISTRY_"}

settings = Settings()
```

- [ ] **Step 3: Create FastAPI app factory**

```python
# services/registry-api/src/registry_api/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .db import init_db
from .routes import tools, namespaces, health

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(
    title="Kiln Registry API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(tools.router, prefix="/tools", tags=["tools"])
app.include_router(namespaces.router, prefix="/namespaces", tags=["namespaces"])
```

- [ ] **Step 4: Commit**

```bash
git add services/registry-api/
git commit -m "feat: scaffold registry-api service with FastAPI + config"
```

---

### Task 7: Database models + migrations for Registry DB

**Files:**
- Create: `services/registry-api/src/registry_api/db.py`
- Create: `services/registry-api/src/registry_api/models.py`
- Create: `services/registry-api/alembic.ini`
- Create: `services/registry-api/alembic/env.py`

- [ ] **Step 1: Create async SQLAlchemy engine**

```python
# services/registry-api/src/registry_api/db.py
from collections.abc import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    """Called on startup — verify connection."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
```

- [ ] **Step 2: Create SQLAlchemy ORM models**

```python
# services/registry-api/src/registry_api/models.py
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Integer, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB, ARRAY, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Namespace(Base):
    __tablename__ = "namespaces"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # e.g. "com.kiln.tools"
    owner_uid: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    tools: Mapped[list["Tool"]] = relationship(back_populates="namespace_rel")

class Tool(Base):
    __tablename__ = "tools"
    id: Mapped[str] = mapped_column(String(256), primary_key=True)  # com.kiln.tools.weather
    namespace_id: Mapped[str] = mapped_column(ForeignKey("namespaces.id"))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    latest_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    category: Mapped[str] = mapped_column(String(64), default="general")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=[])
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    search_vector: Mapped[str] = mapped_column(TSVECTOR, nullable=True)

    namespace_rel: Mapped["Namespace"] = relationship(back_populates="tools")
    versions: Mapped[list["ToolVersion"]] = relationship(back_populates="tool", order_by="ToolVersion.created_at.desc()")

    __table_args__ = (
        Index("ix_tools_search", "search_vector", postgresql_using="gin"),
        Index("ix_tools_tags", "tags", postgresql_using="gin"),
        Index("ix_tools_category", "category"),
    )

class ToolVersion(Base):
    __tablename__ = "tool_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id"))
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    artifact_url: Mapped[str] = mapped_column(String(512))  # GCS signed URL
    requirements_txt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(128))  # Firebase UID

    tool: Mapped["Tool"] = relationship(back_populates="versions")

    __table_args__ = (
        Index("ix_tool_versions_unique", "tool_id", "version", unique=True),
    )
```

- [ ] **Step 3: Set up Alembic for migrations**

```bash
cd services/registry-api
uv run alembic init alembic
```

Update `alembic/env.py` to use async engine and import models.

- [ ] **Step 4: Generate initial migration**

```bash
uv run alembic revision --autogenerate -m "initial registry schema"
```

- [ ] **Step 5: Run migration against local PostgreSQL**

```bash
docker compose up -d postgres
uv run alembic upgrade head
```

- [ ] **Step 6: Write test for model creation**

```python
# services/registry-api/tests/test_models.py
async def test_create_namespace_and_tool(db_session):
    ns = Namespace(id="com.kiln.test", owner_uid="uid123", display_name="Test")
    db_session.add(ns)
    tool = Tool(
        id="com.kiln.test.hello",
        namespace_id="com.kiln.test",
        name="hello",
        description="Says hello",
        spec_json={"id": "com.kiln.test.hello"},
    )
    db_session.add(tool)
    await db_session.commit()
    assert (await db_session.get(Tool, "com.kiln.test.hello")) is not None
```

- [ ] **Step 7: Commit**

```bash
git add services/registry-api/
git commit -m "feat: add Registry DB models with PostgreSQL GIN search + Alembic migrations"
```

---

### Task 8: Registry API CRUD routes

**Files:**
- Create: `services/registry-api/src/registry_api/schemas.py`
- Create: `services/registry-api/src/registry_api/routes/tools.py`
- Create: `services/registry-api/src/registry_api/routes/namespaces.py`
- Create: `services/registry-api/src/registry_api/routes/health.py`
- Create: `services/registry-api/src/registry_api/deps.py`

- [ ] **Step 1: Create Pydantic request/response schemas**

```python
# services/registry-api/src/registry_api/schemas.py
from pydantic import BaseModel

class ToolCreate(BaseModel):
    id: str
    name: str
    description: str
    version: str = "1.0.0"
    category: str = "general"
    tags: list[str] = []
    spec: dict  # Full spec.yaml as dict

class ToolResponse(BaseModel):
    id: str
    name: str
    description: str
    latest_version: str
    category: str
    tags: list[str]
    download_count: int
    json_schema: dict  # LLM-ready tool definition
    created_at: str
    updated_at: str

class ToolListResponse(BaseModel):
    tools: list[ToolResponse]
    total: int
    page: int
    page_size: int

class SearchQuery(BaseModel):
    q: str
    category: str | None = None
    tags: list[str] | None = None
    page: int = 1
    page_size: int = 20
```

- [ ] **Step 2: Create tools router with CRUD + full-text search**

```python
# services/registry-api/src/registry_api/routes/tools.py
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select, func, text
from ..db import get_db
from ..models import Tool, ToolVersion
from ..schemas import ToolCreate, ToolResponse, ToolListResponse, SearchQuery

router = APIRouter()

@router.get("", response_model=ToolListResponse)
async def list_tools(page: int = 1, page_size: int = 50, db=Depends(get_db)):
    ...

@router.get("/search", response_model=ToolListResponse)
async def search_tools(q: str, category: str | None = None, db=Depends(get_db)):
    """Full-text search using PostgreSQL GIN index."""
    stmt = select(Tool).where(
        Tool.search_vector.match(q)  # Uses ts_query
    )
    ...

@router.get("/{tool_id}", response_model=ToolResponse)
async def get_tool(tool_id: str, db=Depends(get_db)):
    ...

@router.post("", response_model=ToolResponse, status_code=201)
async def register_tool(
    spec_file: UploadFile = File(...),
    impl_file: UploadFile = File(...),
    db=Depends(get_db),
):
    """Register a new tool — validates spec, runs fixtures, stores artifacts."""
    ...

@router.post("/{tool_id}/execute")
async def execute_tool(tool_id: str, body: dict, db=Depends(get_db)):
    """Delegates execution to Tool Executor service."""
    ...

@router.delete("/{tool_id}", status_code=204)
async def delete_tool(tool_id: str, db=Depends(get_db)):
    ...

@router.get("/{tool_id}/versions")
async def list_versions(tool_id: str, db=Depends(get_db)):
    ...
```

- [ ] **Step 3: Create health route**

```python
# services/registry-api/src/registry_api/routes/health.py
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"status": "ok", "service": "registry-api"}
```

- [ ] **Step 4: Write integration tests**

```python
# services/registry-api/tests/test_tools_api.py
import pytest
from httpx import AsyncClient, ASGITransport
from registry_api.main import app

@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

async def test_list_tools_empty(client):
    resp = await client.get("/tools")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

async def test_register_and_get_tool(client):
    # Upload spec.yaml + impl.py
    ...

async def test_search_tools(client):
    # Register tool, then search by keyword
    ...
```

- [ ] **Step 5: Run tests**

```bash
uv run --package registry-api pytest -v
```

- [ ] **Step 6: Commit**

```bash
git add services/registry-api/
git commit -m "feat: add Registry API routes — CRUD, full-text search, version management"
```

---

### Task 9: Firebase Auth middleware for Registry API

**Files:**
- Create: `services/registry-api/src/registry_api/auth.py`
- Modify: `services/registry-api/src/registry_api/routes/tools.py` (add auth dependency)

- [ ] **Step 1: Create Firebase JWT verification middleware**

```python
# services/registry-api/src/registry_api/auth.py
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import firebase_admin
from firebase_admin import auth as firebase_auth, credentials

_bearer = HTTPBearer(auto_error=False)

def _init_firebase():
    if not firebase_admin._apps:
        firebase_admin.initialize_app()

async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Verify Firebase JWT and return user claims."""
    if creds is None:
        raise HTTPException(401, "Missing authorization header")
    _init_firebase()
    try:
        decoded = firebase_auth.verify_id_token(creds.credentials)
        return {"uid": decoded["uid"], "email": decoded.get("email", "")}
    except Exception:
        raise HTTPException(401, "Invalid or expired token")

async def optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict | None:
    """Returns user if authenticated, None otherwise. For public endpoints."""
    if creds is None:
        return None
    try:
        return await get_current_user(creds)
    except HTTPException:
        return None
```

- [ ] **Step 2: Add auth to write endpoints**

Update `routes/tools.py`:
- `GET /tools` and `GET /tools/{id}` — public (use `optional_user`)
- `POST /tools` — requires auth (use `get_current_user`)
- `DELETE /tools/{id}` — requires auth + ownership check
- `POST /tools/{id}/execute` — requires auth

- [ ] **Step 3: Write auth tests with mocked Firebase**

- [ ] **Step 4: Commit**

```bash
git add services/registry-api/src/registry_api/auth.py
git commit -m "feat: add Firebase Auth JWT middleware to Registry API"
```

---

## Chunk 3: Tool Executor + Synthesis Service

### Task 10: Scaffold Tool Executor service

**Files:**
- Create: `services/tool-executor/pyproject.toml`
- Create: `services/tool-executor/src/tool_executor/main.py`
- Create: `services/tool-executor/src/tool_executor/config.py`
- Create: `services/tool-executor/src/tool_executor/sandbox.py`
- Create: `services/tool-executor/src/tool_executor/installer.py`
- Create: `services/tool-executor/src/tool_executor/routes/execute.py`
- Create: `services/tool-executor/src/tool_executor/routes/health.py`
- Create: `services/tool-executor/Dockerfile`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "tool-executor"
version = "0.1.0"
description = "Kiln Tool Executor — gVisor-sandboxed tool execution"
requires-python = ">=3.13"
dependencies = [
    "kiln-core",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "pydantic-settings>=2.6",
    "httpx>=0.28",
    "google-cloud-storage>=2.18",
]
```

- [ ] **Step 2: Create sandbox execution engine**

```python
# services/tool-executor/src/tool_executor/sandbox.py
import asyncio
import tempfile
import json
from pathlib import Path
from pydantic import BaseModel

class ExecuteRequest(BaseModel):
    impl_code: str
    function_name: str
    arguments: dict
    requirements: list[str] | None = None
    timeout: int = 30

class ExecutionError(Exception):
    pass

class SandboxExecutor:
    """Executes tool code in an isolated environment.

    On Cloud Run, gVisor provides the sandbox natively.
    Locally, uses async subprocess isolation with restricted imports.
    """
    BLOCKED_PACKAGES = {"os", "subprocess", "shutil", "socket", "ctypes", "importlib"}

    async def execute(self, request: ExecuteRequest) -> dict:
        """Execute tool in isolated subprocess (async, non-blocking)."""
        with tempfile.TemporaryDirectory() as workdir:
            if request.requirements:
                await self._install_deps(workdir, request.requirements)
            tool_path = Path(workdir) / "tool.py"
            tool_path.write_text(request.impl_code)
            runner = self._build_runner(request.function_name, request.arguments)
            runner_path = Path(workdir) / "runner.py"
            runner_path.write_text(runner)
            # Use asyncio subprocess to avoid blocking the event loop
            proc = await asyncio.create_subprocess_exec(
                "python", str(runner_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=request.timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                raise ExecutionError("Tool execution timed out")
            if proc.returncode != 0:
                raise ExecutionError(stderr.decode())
            return json.loads(stdout.decode())

    def _build_runner(self, fn_name: str, args: dict) -> str:
        args_json = json.dumps(args)
        return f'import json,sys;sys.path.insert(0,".");from tool import {fn_name};print(json.dumps({fn_name}(**json.loads({args_json!r}))))'
```

- [ ] **Step 3: Create execute route**

```python
# services/tool-executor/src/tool_executor/routes/execute.py
from fastapi import APIRouter
from ..sandbox import SandboxExecutor

router = APIRouter()
executor = SandboxExecutor()

@router.post("/execute")
async def execute_tool(body: ExecuteRequest):
    """Receives tool code + args, executes in sandbox, returns result."""
    result = await executor.execute(
        impl_code=body.impl_code,
        function_name=body.function_name,
        arguments=body.arguments,
        requirements=body.requirements,
        timeout=body.timeout,
    )
    return {"status": "success", "result": result}
```

- [ ] **Step 4: Create Dockerfile for Cloud Run with gVisor**

```dockerfile
# services/tool-executor/Dockerfile
FROM python:3.13-slim

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Pre-install popular packages for faster cold starts
RUN pip install --no-cache-dir requests httpx beautifulsoup4 pandas numpy

COPY packages/core /app/packages/core
COPY services/tool-executor /app/services/tool-executor

RUN cd /app/services/tool-executor && uv sync --no-dev

EXPOSE 8400
CMD ["uv", "run", "uvicorn", "tool_executor.main:app", "--host", "0.0.0.0", "--port", "8400"]
```

- [ ] **Step 5: Write tests**

- [ ] **Step 6: Commit**

```bash
git add services/tool-executor/
git commit -m "feat: add Tool Executor service — sandboxed execution with blocked-package validation"
```

---

### Task 11: Scaffold Synthesis Service with Claude API + Pub/Sub

**Files:**
- Create: `services/synthesis/pyproject.toml`
- Create: `services/synthesis/src/synthesis/main.py`
- Create: `services/synthesis/src/synthesis/config.py`
- Create: `services/synthesis/src/synthesis/consumer.py`
- Create: `services/synthesis/src/synthesis/generator.py`
- Create: `services/synthesis/src/synthesis/validator.py`
- Create: `services/synthesis/src/synthesis/publisher.py`
- Create: `services/synthesis/Dockerfile`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "synthesis"
version = "0.1.0"
description = "Kiln Synthesis Service — Claude API tool generation + Pub/Sub consumer"
requires-python = ">=3.13"
dependencies = [
    "kiln-core",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "anthropic>=1.0",
    "google-cloud-pubsub>=2.24",
    "google-cloud-storage>=2.18",
    "httpx>=0.28",
    "pydantic-settings>=2.6",
    "pyyaml>=6.0",
]
```

- [ ] **Step 2: Create Claude API tool generator**

```python
# services/synthesis/src/synthesis/generator.py
import anthropic
from .config import settings

class ToolGenerator:
    """Uses Claude API to synthesize tool spec.yaml + impl.py from description."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def generate(self, request: SynthesisRequest) -> SynthesisResult:
        """Generate spec.yaml + impl.py + requirements.txt for a tool."""
        prompt = self._build_prompt(request)
        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=SYNTHESIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return self._parse_response(response)

    def _build_prompt(self, request: SynthesisRequest) -> str:
        return f"""Generate a Kiln tool with the following specification:

Tool Name: {request.tool_name}
Description: {request.description}
Inputs: {json.dumps([i.model_dump() for i in request.inputs])}
Expected Output: {json.dumps(request.output.model_dump() if request.output else {})}

Generate exactly three files:
1. spec.yaml — following the Kiln tool spec format
2. impl.py — pure Python implementation with REQUIRED_ENV_VARS list
3. requirements.txt — any pip dependencies needed

Rules:
- Use free, public APIs when possible
- Handle errors gracefully and return error dicts, never raise
- Include test fixtures in spec.yaml
- Declare all required environment variables in REQUIRED_ENV_VARS
- Do NOT use blocked packages: {', '.join(BLOCKED_PACKAGES)}
"""

SYNTHESIS_SYSTEM_PROMPT = """You are a tool synthesis engine for the Kiln platform.
You generate Python tools that conform to the Kiln spec format.
Each tool must have a spec.yaml, impl.py, and requirements.txt.
Tools must be self-contained, well-tested, and use only safe packages."""
```

- [ ] **Step 2b: Create Pydantic schemas for synthesis**

```python
# services/synthesis/src/synthesis/schemas.py
from pydantic import BaseModel

class SynthesisInput(BaseModel):
    name: str
    type: str
    description: str = ""
    required: bool = True

class SynthesisOutput(BaseModel):
    type: str = "object"
    fields: list[SynthesisInput] = []

class SynthesisRequest(BaseModel):
    job_id: str
    tool_name: str
    description: str
    inputs: list[SynthesisInput] = []
    output: SynthesisOutput | None = None
    callback_url: str | None = None

class SynthesisResult(BaseModel):
    tool_id: str
    spec_yaml: str
    impl_py: str
    requirements_txt: str = ""
    env_vars: list[str] = []
```

- [ ] **Step 3: Create Pub/Sub consumer (async-safe)**

```python
# services/synthesis/src/synthesis/consumer.py
import asyncio
from google.cloud import pubsub_v1
from .config import settings
from .schemas import SynthesisRequest
from .generator import ToolGenerator
from .validator import ToolValidator
from .publisher import publish_to_registry

async def start_consumer():
    """Subscribe to synthesis-requests topic and process messages."""
    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(
        settings.gcp_project_id,
        settings.synthesis_subscription,
    )
    loop = asyncio.get_event_loop()

    def callback(message):
        """Sync callback — dispatches async work to the event loop."""
        request = SynthesisRequest.model_validate_json(message.data)
        future = asyncio.run_coroutine_threadsafe(_process(request), loop)
        try:
            future.result(timeout=120)
            message.ack()
        except Exception:
            message.nack()

    subscriber.subscribe(subscription_path, callback=callback)

async def _process(request: SynthesisRequest):
    generator = ToolGenerator()
    result = await generator.generate(request)
    if ToolValidator().validate(result):
        await publish_to_registry(result)
```

- [ ] **Step 4: Create validator (schema + fixture testing)**

```python
# services/synthesis/src/synthesis/validator.py
from kiln_core import KilnLoader

class ToolValidator:
    def validate(self, result: SynthesisResult) -> bool:
        """Validates spec.yaml schema + runs test fixtures."""
        loader = KilnLoader(auto_register=False)
        # 1. Validate spec against JSON schema
        # 2. Run test fixtures
        # 3. Check for blocked packages in requirements.txt
        return all_checks_passed
```

- [ ] **Step 5: Create webhook publisher to Registry API**

```python
# services/synthesis/src/synthesis/publisher.py
import httpx
from .config import settings

async def publish_to_registry(result: SynthesisResult):
    """POST synthesized tool to Registry API."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.registry_api_url}/tools",
            files={
                "spec_file": ("spec.yaml", result.spec_yaml),
                "impl_file": ("impl.py", result.impl_py),
            },
        )
        resp.raise_for_status()
```

- [ ] **Step 6: Write tests**

- [ ] **Step 7: Commit**

```bash
git add services/synthesis/
git commit -m "feat: add Synthesis Service — Claude API code gen + Pub/Sub consumer + validation"
```

---

## Chunk 4: Chat Backend Service

### Task 12: Scaffold Chat Backend with Planning Agent + Graph Executor

**Files:**
- Create: `services/chat-backend/pyproject.toml`
- Create: `services/chat-backend/src/chat_backend/main.py`
- Create: `services/chat-backend/src/chat_backend/config.py`
- Create: `services/chat-backend/src/chat_backend/db.py`
- Create: `services/chat-backend/src/chat_backend/models.py`
- Create: `services/chat-backend/src/chat_backend/auth.py`
- Create: `services/chat-backend/src/chat_backend/planner.py`
- Create: `services/chat-backend/src/chat_backend/graph_executor.py`
- Create: `services/chat-backend/src/chat_backend/key_vault.py`
- Create: `services/chat-backend/src/chat_backend/routes/chat.py`
- Create: `services/chat-backend/src/chat_backend/routes/sessions.py`
- Create: `services/chat-backend/src/chat_backend/routes/health.py`
- Create: `services/chat-backend/Dockerfile`
- Create: `services/chat-backend/alembic/`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "chat-backend"
version = "0.1.0"
description = "Kiln Chat Backend — LLM Planning Agent, Graph Executor, SSE streaming"
requires-python = ">=3.13"
dependencies = [
    "kiln-core",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "anthropic>=1.0",
    "google-cloud-pubsub>=2.24",
    "firebase-admin>=6.6",
    "pydantic-settings>=2.6",
    "httpx>=0.28",
    "cryptography>=44.0",
]
```

- [ ] **Step 2: Create Chat DB models**

```python
# services/chat-backend/src/chat_backend/models.py
class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    user_uid: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512), default="New Chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    messages: Mapped[list["Message"]] = relationship(back_populates="session")

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    role: Mapped[str] = mapped_column(String(16))  # "user"|"assistant"|"system"
    content: Mapped[str] = mapped_column(Text)
    task_graph: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    session: Mapped["Session"] = relationship(back_populates="messages")

class KeyVaultEntry(Base):
    __tablename__ = "key_vault"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_uid: Mapped[str] = mapped_column(String(128), index=True)
    key_name: Mapped[str] = mapped_column(String(128))  # e.g. "OPENWEATHER_API_KEY"
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (Index("ix_vault_user_key", "user_uid", "key_name", unique=True),)
```

- [ ] **Step 3: Create LLM Planning Agent (migrate from aria/planner.py)**

Port `aria/planner.py` to `services/chat-backend/src/chat_backend/planner.py`:
- Switch from Mistral to Claude API (as specified in PDF: "Claude API code generation")
- Modernize with Pydantic v2 structured output
- Use `anthropic` SDK instead of `mistralai`
- Keep task graph output format (nodes, edges, entry_nodes, exit_node, missing_tools)

```python
# services/chat-backend/src/chat_backend/planner.py
import anthropic
from .config import settings

class PlanningAgent:
    """Decomposes natural language requests into DAGs of subtasks."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def plan(self, user_request: str, available_tools: list[dict]) -> TaskGraph:
        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=PLANNER_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Request: {user_request}\n\nAvailable tools:\n{json.dumps(available_tools)}"
            }],
        )
        return TaskGraph.model_validate_json(response.content[0].text)
```

- [ ] **Step 4: Create Graph Executor (migrate from aria/graph_flow.py)**

Port `aria/graph_flow.py` to `services/chat-backend/src/chat_backend/graph_executor.py`:
- Keep Kahn's algorithm for topological sort
- Replace AG2 dependency with direct Claude API tool-use for each node
- Execute tools by calling Tool Executor service via HTTP (not local execution)
- Stream events via SSE queue
- Publish synthesis requests to Pub/Sub when tools are missing

```python
# services/chat-backend/src/chat_backend/graph_executor.py
import asyncio
import httpx
import anthropic
from collections import deque

class GraphExecutor:
    """Executes task graphs using Claude API tool-use + Kiln Tool Executor."""

    def __init__(self, registry_api_url: str, executor_url: str):
        self.registry = registry_api_url
        self.executor = executor_url
        self.client = anthropic.Anthropic()

    async def execute(
        self,
        graph: TaskGraph,
        event_queue: asyncio.Queue,
        user_env: dict[str, str] | None = None,
    ) -> str:
        order = self._topo_sort(graph.nodes, graph.edges)
        results: dict[str, str] = {}

        for node_id in order:
            node = graph.nodes_by_id[node_id]
            await event_queue.put({"type": "node_start", "node_id": node_id})

            # Get tool definitions from Registry API
            tools = await self._fetch_tools(node.tools)

            # Build context from upstream results
            context = self._build_context(node, results, graph.edges)

            # Execute node using Claude with tool-use
            result = await self._run_node(node, tools, context, event_queue)
            results[node_id] = result

            await event_queue.put({"type": "node_complete", "node_id": node_id, "result": result})

        final = results.get(graph.exit_node, "")
        await event_queue.put({"type": "flow_complete", "final_answer": final})
        return final

    def _topo_sort(self, nodes, edges) -> list[str]:
        """Kahn's algorithm."""
        ...

    async def _run_node(self, node, tools, context, queue) -> str:
        """Use Claude tool-use to execute a single node."""
        ...

    async def _execute_tool(self, tool_id: str, arguments: dict) -> dict:
        """Call Tool Executor service to run a tool in sandbox."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.executor}/execute", json={...})
            return resp.json()
```

- [ ] **Step 5: Create encrypted Key Vault**

```python
# services/chat-backend/src/chat_backend/key_vault.py
from cryptography.fernet import Fernet
from .config import settings

class KeyVault:
    """Encrypts and stores user API keys in PostgreSQL."""

    def __init__(self):
        self.cipher = Fernet(settings.vault_encryption_key.encode())

    async def store(self, db, user_uid: str, key_name: str, value: str):
        encrypted = self.cipher.encrypt(value.encode())
        entry = KeyVaultEntry(user_uid=user_uid, key_name=key_name, encrypted_value=encrypted)
        db.add(entry)
        await db.commit()

    async def retrieve(self, db, user_uid: str, key_name: str) -> str | None:
        entry = await db.execute(
            select(KeyVaultEntry).where(
                KeyVaultEntry.user_uid == user_uid,
                KeyVaultEntry.key_name == key_name,
            )
        )
        row = entry.scalar_one_or_none()
        if row:
            return self.cipher.decrypt(row.encrypted_value).decode()
        return None
```

- [ ] **Step 6: Create Redis-backed run store + SSE streaming chat route**

```python
# services/chat-backend/src/chat_backend/run_store.py
"""In-memory run store (swap for Redis in production via config)."""
import asyncio
from dataclasses import dataclass, field

@dataclass
class RunState:
    graph: dict
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)

_runs: dict[str, RunState] = {}

def store_run(run_id: str, graph: dict) -> None:
    _runs[run_id] = RunState(graph=graph)

def get_run(run_id: str) -> RunState | None:
    return _runs.get(run_id)

def get_event_queue(run_id: str) -> asyncio.Queue | None:
    run = _runs.get(run_id)
    return run.event_queue if run else None
```

```python
# services/chat-backend/src/chat_backend/routes/chat.py
import asyncio
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from ..planner import PlanningAgent
from ..graph_executor import GraphExecutor
from ..auth import get_current_user
from ..run_store import store_run, get_run, get_event_queue
from ..config import settings

router = APIRouter()

class ChatRequest(BaseModel):
    message: str

class ExecuteRequest(BaseModel):
    env_vars: dict[str, str] = {}

def _get_executor() -> GraphExecutor:
    return GraphExecutor(
        registry_api_url=settings.registry_api_url,
        executor_url=settings.tool_executor_url,
    )

@router.post("/chat")
async def start_chat(body: ChatRequest, user=Depends(get_current_user)):
    """Phase 1: Plan the request, return run_id + task graph."""
    planner = PlanningAgent()
    tools = await planner.fetch_available_tools()
    graph = await planner.plan(body.message, tools)
    run_id = str(uuid.uuid4())
    store_run(run_id, graph.model_dump())
    return {"run_id": run_id, "graph": graph.model_dump()}

@router.post("/chat/{run_id}/execute")
async def execute_chat(run_id: str, body: ExecuteRequest, user=Depends(get_current_user)):
    """Phase 2: Execute the plan with provided env vars."""
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    executor = _get_executor()
    asyncio.create_task(executor.execute(run.graph, run.event_queue, body.env_vars))
    return {"status": "started"}

@router.get("/chat/{run_id}/stream")
async def stream_events(run_id: str, token: str = Query(None)):
    """SSE stream of execution events.

    Note: Uses query param ?token=<jwt> for auth since EventSource
    does not support custom headers. Validate token server-side.
    """
    queue = get_event_queue(run_id)
    if not queue:
        raise HTTPException(404, "Run not found")

    async def event_generator():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

- [ ] **Step 7: Set up Alembic for Chat DB**

- [ ] **Step 8: Write tests**

- [ ] **Step 9: Commit**

```bash
git add services/chat-backend/
git commit -m "feat: add Chat Backend — Planning Agent, Graph Executor, Key Vault, SSE streaming"
```

---

## Chunk 5: MCP Server

### Task 13: Build MCP Server (JSON-RPC ↔ Registry API adapter)

This is a key differentiator — the MCP Server exposes the entire Kiln tool registry to any MCP-compatible AI client (Claude Desktop, Cursor, VS Code Copilot, Windsurf).

**Files:**
- Create: `services/mcp-server/pyproject.toml`
- Create: `services/mcp-server/src/mcp_server/main.py`
- Create: `services/mcp-server/src/mcp_server/config.py`
- Create: `services/mcp-server/src/mcp_server/auth.py`
- Create: `services/mcp-server/src/mcp_server/protocol.py`
- Create: `services/mcp-server/src/mcp_server/handlers.py`
- Create: `services/mcp-server/src/mcp_server/registry_client.py`
- Create: `services/mcp-server/Dockerfile`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "mcp-server"
version = "0.1.0"
description = "Kiln MCP Server — JSON-RPC adapter bridging MCP protocol to Registry API"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "httpx>=0.28",
    "pydantic>=2.0",
    "pydantic-settings>=2.6",
]
```

- [ ] **Step 2: Create MCP JSON-RPC protocol handler**

```python
# services/mcp-server/src/mcp_server/protocol.py
"""MCP (Model Context Protocol) v2025-11-25 JSON-RPC message handling."""
from pydantic import BaseModel

class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict | None = None

class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None
    result: dict | None = None
    error: dict | None = None

class McpCapabilities(BaseModel):
    tools: dict = {"listChanged": True}

class McpServerInfo(BaseModel):
    name: str = "kiln"
    version: str = "0.1.0"

def make_response(req_id, result):
    return JsonRpcResponse(id=req_id, result=result)

def make_error(req_id, code, message):
    return JsonRpcResponse(id=req_id, error={"code": code, "message": message})
```

- [ ] **Step 3: Create MCP method handlers**

```python
# services/mcp-server/src/mcp_server/handlers.py
from .registry_client import RegistryClient
from .protocol import make_response, make_error

class McpHandlers:
    def __init__(self, registry: RegistryClient):
        self.registry = registry

    async def handle(self, method: str, params: dict | None, req_id) -> dict:
        match method:
            case "initialize":
                return make_response(req_id, {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "kiln", "version": "0.1.0"},
                })
            case "tools/list":
                tools = await self.registry.list_tools()
                return make_response(req_id, {
                    "tools": [self._to_mcp_tool(t) for t in tools]
                })
            case "tools/call":
                tool_name = params["name"]
                arguments = params.get("arguments", {})
                result = await self.registry.execute_tool(tool_name, arguments)
                return make_response(req_id, {
                    "content": [{"type": "text", "text": json.dumps(result)}]
                })
            case _:
                return make_error(req_id, -32601, f"Unknown method: {method}")

    def _to_mcp_tool(self, tool: dict) -> dict:
        """Convert Kiln tool spec to MCP tool format."""
        return {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["json_schema"],
        }
```

- [ ] **Step 4: Create SSE transport endpoint**

```python
# services/mcp-server/src/mcp_server/main.py
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from .handlers import McpHandlers
from .registry_client import RegistryClient

app = FastAPI(title="Kiln MCP Server")
handlers = McpHandlers(RegistryClient())

@app.get("/sse")
async def sse_endpoint(request: Request):
    """MCP SSE transport — clients connect here for bidirectional JSON-RPC."""
    import uuid
    session_id = str(uuid.uuid4())
    _sessions[session_id] = asyncio.Queue()

    async def event_stream():
        # Send endpoint URL for client to POST messages to
        yield f"event: endpoint\ndata: /messages/{session_id}\n\n"
        # Keep connection alive, forward responses and notifications
        queue = _sessions[session_id]
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"event: message\ndata: {json.dumps(event)}\n\n"
        finally:
            _sessions.pop(session_id, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

# In-memory session store (maps session_id → response queue)
_sessions: dict[str, asyncio.Queue] = {}

@app.post("/messages/{session_id}")
async def handle_message(session_id: str, request: Request):
    """Receive JSON-RPC messages from MCP client."""
    body = await request.json()
    rpc_request = JsonRpcRequest.model_validate(body)
    response = await handlers.handle(rpc_request.method, rpc_request.params, rpc_request.id)
    return response.model_dump(exclude_none=True)
```

- [ ] **Step 5: Create Registry API HTTP client**

```python
# services/mcp-server/src/mcp_server/registry_client.py
import httpx
from .config import settings

class RegistryClient:
    """HTTP client for Kiln Registry API."""

    async def list_tools(self) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{settings.registry_api_url}/tools")
            return resp.json()["tools"]

    async def execute_tool(self, tool_id: str, arguments: dict) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.registry_api_url}/tools/{tool_id}/execute",
                json=arguments,
            )
            return resp.json()
```

- [ ] **Step 6: Add OAuth 2.1 + PKCE authentication**

- [ ] **Step 7: Add notifications/tools/list_changed support**

When a new tool is synthesized and registered, the MCP Server should emit a `notifications/tools/list_changed` notification to all connected clients, prompting them to refresh their tool catalog.

- [ ] **Step 8: Write tests**

- [ ] **Step 9: Commit**

```bash
git add services/mcp-server/
git commit -m "feat: add MCP Server — JSON-RPC adapter with tools/list, tools/call, list_changed notifications"
```

---

## Chunk 6: React Frontend Apps

### Task 14: Scaffold Chat UI with React 19 + Vite + Tailwind + shadcn/ui

**Files:**
- Create: `apps/chat-ui/` (full Vite + React 19 + TypeScript + Tailwind v4 + shadcn/ui setup)

- [ ] **Step 1: Initialize Vite project**

```bash
cd apps && pnpm create vite chat-ui --template react-ts
cd chat-ui
pnpm add @tanstack/react-query @tanstack/react-router zustand
pnpm add -D tailwindcss @tailwindcss/vite
```

- [ ] **Step 2: Set up Tailwind CSS v4**

```css
/* apps/chat-ui/src/index.css */
@import "tailwindcss";
```

```typescript
// apps/chat-ui/vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8200" } },
});
```

- [ ] **Step 3: Set up shadcn/ui components**

```bash
cd apps/chat-ui && npx shadcn@latest init
npx shadcn@latest add button input card badge scroll-area separator avatar
```

- [ ] **Step 4: Create Zustand chat store**

```typescript
// apps/chat-ui/src/stores/chat-store.ts
import { create } from "zustand";

interface NodeState {
  id: string;
  role: string;
  task: string;
  tools: string[];
  status: "pending" | "running" | "complete" | "error";
  result?: string;
  toolCalls: { tool: string; args: Record<string, unknown>; result?: unknown }[];
}

interface ChatState {
  phase: "idle" | "planning" | "config" | "running" | "complete" | "error";
  nodes: Record<string, NodeState>;
  nodeOrder: string[];
  logs: LogEntry[];
  finalAnswer: string;
  error: string;
  // Actions
  reset: () => void;
  setPlanning: () => void;
  setPlanReady: (graph: TaskGraph) => void;
  setNodeStart: (nodeId: string) => void;
  setNodeComplete: (nodeId: string, result: string) => void;
  setFlowComplete: (answer: string) => void;
  setError: (error: string) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  // ... state + actions migrated from aria-ui's useReducer
}));
```

- [ ] **Step 5: Create Firebase Auth hook**

```typescript
// apps/chat-ui/src/lib/auth.ts
import { initializeApp } from "firebase/app";
import { getAuth, signInWithPopup, GoogleAuthProvider, onAuthStateChanged, User } from "firebase/auth";
import { create } from "zustand";

const firebaseConfig = { /* from env */ };
const firebaseApp = initializeApp(firebaseConfig);
const auth = getAuth(firebaseApp);

interface AuthState {
  user: User | null;
  loading: boolean;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  loading: true,
  signIn: async () => {
    await signInWithPopup(auth, new GoogleAuthProvider());
  },
  signOut: async () => {
    await auth.signOut();
    set({ user: null });
  },
}));

// Listen for auth state changes
onAuthStateChanged(auth, (user) => {
  useAuth.setState({ user, loading: false });
});
```

- [ ] **Step 6: Create API client with SSE support**

```typescript
// apps/chat-ui/src/lib/api.ts
import { useAuth } from "./auth";

const BASE = import.meta.env.VITE_CHAT_API_URL || "/api";

async function fetchWithAuth(path: string, options?: RequestInit) {
  const user = useAuth.getState().user;
  const token = user ? await user.getIdToken() : null;
  return fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      ...options?.headers,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "Content-Type": "application/json",
    },
  });
}

export async function startChat(message: string) {
  const resp = await fetchWithAuth("/chat", {
    method: "POST",
    body: JSON.stringify({ message }),
  });
  return resp.json();
}

export async function connectSSE(runId: string, onEvent: (event: any) => void): Promise<AbortController> {
  // EventSource does not support custom headers, so we pass the JWT as a
  // query param and use fetch + ReadableStream for authenticated SSE.
  const user = useAuth.getState().user;
  const token = user ? await user.getIdToken() : "";
  const controller = new AbortController();
  const resp = await fetch(`${BASE}/chat/${runId}/stream?token=${token}`, {
    signal: controller.signal,
  });
  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  (async () => {
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop()!;
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          onEvent(JSON.parse(line.slice(6)));
        }
      }
    }
  })();
  return controller; // call controller.abort() to disconnect
}
```

- [ ] **Step 7: Create main App component with DAG view**

Migrate and modernize the UI from `aria-ui/src/App.tsx`:
- Use shadcn/ui components instead of raw CSS
- Use Zustand store instead of useReducer
- Use TanStack Router for routing (login, chat)
- Keep DAG visualization, node cards, log panel
- Add dark/light mode toggle

- [ ] **Step 8: Write component tests with Vitest**

- [ ] **Step 9: Commit**

```bash
git add apps/chat-ui/
git commit -m "feat: add Chat UI — React 19 + Tailwind v4 + shadcn/ui + SSE streaming"
```

---

### Task 15: Scaffold Registry UI

**Files:**
- Create: `apps/registry-ui/` (full Vite + React 19 + TypeScript + Tailwind v4 + shadcn/ui setup)

- [ ] **Step 1: Initialize Vite project**

```bash
cd apps && pnpm create vite registry-ui --template react-ts
```

- [ ] **Step 2: Set up Tailwind + shadcn/ui (same as chat-ui)**

- [ ] **Step 3: Create Registry API client**

```typescript
// apps/registry-ui/src/lib/api.ts
export async function listTools(params?: { q?: string; category?: string; page?: number }) {
  const query = new URLSearchParams(params as any).toString();
  const resp = await fetchWithAuth(`/tools?${query}`);
  return resp.json();
}

export async function getToolDetail(toolId: string) { ... }
export async function publishTool(specFile: File, implFile: File) { ... }
export async function getVersionHistory(toolId: string) { ... }
```

- [ ] **Step 4: Create tool browser page**

- Tool card grid with search + category filter
- Full-text search powered by PostgreSQL GIN
- Category badges, download counts, version info
- "Used by" social proof indicators

- [ ] **Step 5: Create tool detail page**

- Spec visualization (inputs, outputs, description)
- Version history timeline
- Live health dashboard (success rate, latency)
- "Try it" playground (execute tool with sample inputs)
- Fork button

- [ ] **Step 6: Create publish workflow page**

- Upload spec.yaml + impl.py
- Live validation feedback
- Fixture test results
- Namespace selection
- Publish confirmation

- [ ] **Step 7: Write tests**

- [ ] **Step 8: Commit**

```bash
git add apps/registry-ui/
git commit -m "feat: add Registry UI — tool browser, detail pages, publish workflow"
```

---

## Chunk 7: Infrastructure + CI/CD

### Task 16: Create Terraform configuration for GCP

**Files:**
- Create: `infra/terraform/main.tf`
- Create: `infra/terraform/cloud-run.tf`
- Create: `infra/terraform/cloud-sql.tf`
- Create: `infra/terraform/storage.tf`
- Create: `infra/terraform/pubsub.tf`
- Create: `infra/terraform/firebase.tf`
- Create: `infra/terraform/iam.tf`
- Create: `infra/terraform/secrets.tf`
- Create: `infra/terraform/networking.tf`
- Create: `infra/terraform/variables.tf`
- Create: `infra/terraform/outputs.tf`

- [ ] **Step 1: Create main.tf with provider config**

```hcl
# infra/terraform/main.tf
terraform {
  required_version = ">= 1.9"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 6.0" }
  }
  backend "gcs" {
    bucket = "kiln-terraform-state"
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
```

- [ ] **Step 2: Create Cloud Run service definitions**

```hcl
# infra/terraform/cloud-run.tf
locals {
  services = {
    "chat-backend"  = { port = 8200, cpu = "1", memory = "512Mi", min = 1, max = 5 }
    "registry-api"  = { port = 8100, cpu = "1", memory = "512Mi", min = 1, max = 5 }
    "mcp-server"    = { port = 8300, cpu = "0.5", memory = "256Mi", min = 1, max = 10 }
    "tool-executor" = { port = 8400, cpu = "1", memory = "512Mi", min = 0, max = 100 }
    "synthesis"     = { port = 8500, cpu = "2", memory = "1Gi", min = 0, max = 20 }
  }
}

resource "google_cloud_run_v2_service" "services" {
  for_each = local.services
  name     = "kiln-${each.key}"
  location = var.region

  template {
    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/kiln/${each.key}:latest"
      ports { container_port = each.value.port }
      resources {
        limits = { cpu = each.value.cpu, memory = each.value.memory }
      }
      # Environment variables from Secret Manager
      dynamic "env" {
        for_each = var.service_env_vars[each.key]
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value.secret
              version = "latest"
            }
          }
        }
      }
    }
    scaling {
      min_instance_count = each.value.min
      max_instance_count = each.value.max
    }
    vpc_access {
      connector = google_vpc_access_connector.connector.id
    }
  }
}
```

- [ ] **Step 3: Create Cloud SQL instances**

```hcl
# infra/terraform/cloud-sql.tf
resource "google_sql_database_instance" "registry_db" {
  name             = "kiln-registry-db"
  database_version = "POSTGRES_16"
  region           = var.region
  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }
  }
}

resource "google_sql_database_instance" "chat_db" {
  name             = "kiln-chat-db"
  database_version = "POSTGRES_16"
  region           = var.region
  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }
  }
}
```

- [ ] **Step 4: Create GCS, Pub/Sub, Firebase, Secrets, IAM, Networking**

- [ ] **Step 5: Create variables.tf + outputs.tf + environment tfvars**

- [ ] **Step 6: Commit**

```bash
git add infra/terraform/
git commit -m "feat: add Terraform config for GCP — Cloud Run, Cloud SQL, GCS, Pub/Sub, Firebase"
```

---

### Task 17: Create Dockerfiles for all services

**Files:**
- Create: `infra/docker/base-python.Dockerfile`
- Create: `services/registry-api/Dockerfile`
- Create: `services/chat-backend/Dockerfile`
- Create: `services/mcp-server/Dockerfile`
- Create: `services/tool-executor/Dockerfile` (already in Task 10)
- Create: `services/synthesis/Dockerfile`

- [ ] **Step 1: Create shared base Python image**

```dockerfile
# infra/docker/base-python.Dockerfile
FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
```

- [ ] **Step 2: Create service Dockerfiles (multi-stage builds)**

Each service follows the same pattern:
```dockerfile
# services/registry-api/Dockerfile
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

COPY packages/core /app/packages/core
COPY services/registry-api /app/services/registry-api

WORKDIR /app/services/registry-api
RUN uv sync --no-dev

EXPOSE 8100
CMD ["uv", "run", "uvicorn", "registry_api.main:app", "--host", "0.0.0.0", "--port", "8100"]
```

- [ ] **Step 3: Update docker-compose.yml with all services**

Add all 5 services + both UIs to docker-compose.yml for local full-stack development.

- [ ] **Step 4: Commit**

```bash
git add infra/docker/ services/*/Dockerfile docker-compose.yml
git commit -m "feat: add Dockerfiles for all services + full-stack docker-compose"
```

---

### Task 18: GitHub Actions CI/CD pipeline

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1: Create CI workflow**

```yaml
# .github/workflows/ci.yml
name: CI
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv run ruff check .
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: pnpm install && pnpm -r lint

  test-python:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env: { POSTGRES_USER: kiln, POSTGRES_PASSWORD: test, POSTGRES_DB: kiln_test }
        ports: ["5432:5432"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-packages
      - run: uv run pytest -v --tb=short

  test-typescript:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: pnpm install && pnpm -r test

  docker-build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service: [registry-api, chat-backend, mcp-server, tool-executor, synthesis]
    steps:
      - uses: actions/checkout@v4
      - run: docker build -f services/${{ matrix.service }}/Dockerfile .
```

- [ ] **Step 2: Create deploy workflow**

```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service: [registry-api, chat-backend, mcp-server, tool-executor, synthesis]
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}
      - uses: google-github-actions/setup-gcloud@v2
      - run: |
          gcloud builds submit \
            --tag ${{ vars.REGION }}-docker.pkg.dev/${{ vars.PROJECT_ID }}/kiln/${{ matrix.service }}:${{ github.sha }} \
            -f services/${{ matrix.service }}/Dockerfile .
          gcloud run deploy kiln-${{ matrix.service }} \
            --image ${{ vars.REGION }}-docker.pkg.dev/${{ vars.PROJECT_ID }}/kiln/${{ matrix.service }}:${{ github.sha }} \
            --region ${{ vars.REGION }}
```

- [ ] **Step 3: Commit**

```bash
git add .github/
git commit -m "feat: add GitHub Actions CI/CD — lint, test, Docker build, Cloud Run deploy"
```

---

## Chunk 8: Observability + Polish

### Task 19: Add OpenTelemetry instrumentation

**Files:**
- Add to each service's dependencies: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`
- Create: `packages/core/src/kiln_core/telemetry.py`

- [ ] **Step 1: Create shared telemetry setup**

```python
# packages/core/src/kiln_core/telemetry.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanExporter
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

def setup_telemetry(app, service_name: str):
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanExporter(CloudTraceSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
```

- [ ] **Step 2: Instrument all services**

Add `setup_telemetry(app, "service-name")` to each service's lifespan.

- [ ] **Step 3: Add structured logging**

```python
import logging
import json

class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "severity": record.levelname,
            "message": record.getMessage(),
            "service": record.name,
            "trace_id": get_current_trace_id(),
        })
```

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/kiln_core/telemetry.py
git commit -m "feat: add OpenTelemetry instrumentation + structured logging"
```

---

### Task 20: Final integration testing + documentation

- [ ] **Step 1: Write end-to-end integration test**

Test the full flow:
1. Register a tool via Registry API
2. Discover it via MCP Server (tools/list)
3. Execute it via MCP Server (tools/call) → Tool Executor
4. Submit a chat request → Chat Backend plans → Graph Executor runs → SSE streams result
5. Trigger synthesis of a missing tool → Pub/Sub → Synthesis Service → Registry API

- [ ] **Step 2: Update README.md**

New README covering:
- What Kiln is (from PDF executive summary)
- Architecture diagram
- Quick start (docker compose up)
- Development setup
- Deployment guide
- MCP client configuration
- Contributing guide

- [ ] **Step 3: Run full test suite**

```bash
task test
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: add integration tests + update README for Kiln monorepo"
```

---

## Service Communication Map

```
┌─────────────┐     ┌──────────────┐     ┌────────────────┐
│  Chat UI    │────▶│ Chat Backend │────▶│  Registry API  │
│ :5173       │ SSE │ :8200        │     │  :8100         │
└─────────────┘     └──────┬───────┘     └───────┬────────┘
                           │                     │
                    Pub/Sub│              ┌──────▼────────┐
                           │              │ Tool Executor │
                    ┌──────▼───────┐      │ :8400         │
                    │  Synthesis   │      └───────────────┘
                    │  :8500       │
                    └──────────────┘

┌──────────────┐     ┌──────────────┐
│ Registry UI  │────▶│ Registry API │
│ :5174        │     │ :8100        │
└──────────────┘     └──────────────┘

┌──────────────┐     ┌──────────────┐
│ MCP Clients  │────▶│  MCP Server  │────▶ Registry API + Tool Executor
│ (Claude, etc)│ SSE │  :8300       │
└──────────────┘     └──────────────┘
```

## Port Assignment

| Service         | Local Port | Cloud Run |
|----------------|-----------|-----------|
| Registry API    | 8100      | kiln-registry-api |
| Chat Backend    | 8200      | kiln-chat-backend |
| MCP Server      | 8300      | kiln-mcp-server |
| Tool Executor   | 8400      | kiln-tool-executor |
| Synthesis       | 8500      | kiln-synthesis |
| Chat UI         | 5173      | Firebase Hosting (chat.kiln.dev) |
| Registry UI     | 5174      | Firebase Hosting (registry.kiln.dev) |
| PostgreSQL      | 5432      | Cloud SQL |
| Redis           | 6379      | Memorystore |
| Pub/Sub Emulator| 8085      | Cloud Pub/Sub |

## Execution Order

Implement tasks in order (1→20). Each task produces a working, testable increment:

1. **Tasks 1-5** (Chunk 1): Monorepo + core library = working `kiln-core` package
2. **Tasks 6-9** (Chunk 2): Registry API = working REST API with PostgreSQL
3. **Tasks 10-11** (Chunk 3): Executor + Synthesis = tool execution + generation
4. **Task 12** (Chunk 4): Chat Backend = planning + execution + SSE
5. **Task 13** (Chunk 5): MCP Server = AI client connectivity
6. **Tasks 14-15** (Chunk 6): Frontend apps = user-facing UI
7. **Tasks 16-18** (Chunk 7): Infrastructure + CI/CD = deployable
8. **Tasks 19-20** (Chunk 8): Observability + polish = production-ready
