# MCP Server OAuth 2.1 + Production Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OAuth 2.1 with PKCE authentication to the Kiln MCP server (delegating to Clerk for identity), inject user env vars into tool execution, and fix all audited production bugs.

**Architecture:** The MCP server becomes an OAuth 2.1 Authorization Server using the MCP SDK's OAuthAuthorizationServerProvider. Clerk handles user login via redirect. In-memory stores hold clients, auth codes, and tokens. Tool execution is refactored to carry user context and inject saved env vars from Clerk.

**Tech Stack:** Python 3.12+, MCP SDK 1.26.0 (mcp.server.auth), FastMCP, Starlette, httpx, Clerk Backend API, pytest

---

## File Structure

### New files
- `packages/mcp_server/kiln_mcp/auth/__init__.py` -- empty package init
- `packages/mcp_server/kiln_mcp/auth/store.py` -- in-memory TTL store for OAuth data
- `packages/mcp_server/kiln_mcp/auth/provider.py` -- OAuthAuthorizationServerProvider implementation
- `packages/mcp_server/kiln_mcp/auth/clerk_callback.py` -- Starlette route for Clerk redirect callback
- `packages/mcp_server/kiln_mcp/tools.py` -- extracted tool registration/execution logic
- `packages/mcp_server/kiln_mcp/user_env.py` -- Clerk env var fetcher with cache
- `packages/mcp_server/tests/test_store.py` -- store unit tests
- `packages/mcp_server/tests/test_user_env.py` -- user env fetcher tests
- `packages/mcp_server/tests/test_provider.py` -- OAuth provider tests
- `packages/mcp_server/tests/test_clerk_callback.py` -- callback route tests

### Modified files
- `packages/mcp_server/kiln_mcp/main.py` -- rewritten: thin entry point wiring auth + tools + health
- `packages/mcp_server/tests/test_handlers.py` -- updated imports (tools module)
- `packages/mcp_server/tests/test_mcp_health.py` -- updated for new health check fields
- `docker-compose.yml` -- Clerk env vars for mcp_server service

---

### Task 1: Create the in-memory OAuth store

**Files:**
- Create: `packages/mcp_server/kiln_mcp/auth/__init__.py`
- Create: `packages/mcp_server/kiln_mcp/auth/store.py`
- Create: `packages/mcp_server/tests/test_store.py`

- [ ] **Step 1: Write the failing tests** -- see spec for InMemoryOAuthStore API: save/get/delete for clients, auth_codes, access_tokens, refresh_tokens. TTL expiry and cleanup. 13 tests total covering all CRUD + expiry + cleanup.

- [ ] **Step 2: Run tests to verify they fail** -- `uv run pytest packages/mcp_server/tests/test_store.py -v` -- expected: ModuleNotFoundError

- [ ] **Step 3: Write the implementation** -- InMemoryOAuthStore with TTL tuples `(data, expires_at)`, lazy expiry on read, active cleanup method.

- [ ] **Step 4: Run tests to verify they pass** -- `uv run pytest packages/mcp_server/tests/test_store.py -v` -- all 13 PASS

- [ ] **Step 5: Commit** -- `git commit -m "Add in-memory OAuth store with TTL expiry"`

---

### Task 2: Create the user env var fetcher

**Files:**
- Create: `packages/mcp_server/kiln_mcp/user_env.py`
- Create: `packages/mcp_server/tests/test_user_env.py`

- [ ] **Step 1: Write the failing tests** -- 4 tests: successful fetch from Clerk, empty on missing CLERK_SECRET_KEY, empty on Clerk error, cache hit on second call.

- [ ] **Step 2: Run tests to verify they fail** -- `uv run pytest packages/mcp_server/tests/test_user_env.py -v` -- expected: ModuleNotFoundError

- [ ] **Step 3: Write the implementation** -- `fetch_user_env_vars(user_id)` calls Clerk Backend API `GET /v1/users/{user_id}`, returns `private_metadata.tool_env_vars`, cached 5 min per user_id.

- [ ] **Step 4: Run tests to verify they pass** -- `uv run pytest packages/mcp_server/tests/test_user_env.py -v` -- all 4 PASS

- [ ] **Step 5: Commit** -- `git commit -m "Add user env var fetcher with Clerk Backend API and caching"`

---

### Task 3: Extract tools module from main.py

**Files:**
- Create: `packages/mcp_server/kiln_mcp/tools.py`
- Modify: `packages/mcp_server/kiln_mcp/main.py`
- Modify: `packages/mcp_server/tests/test_handlers.py`

- [ ] **Step 1: Create tools.py** -- Extract from main.py: `_build_mcp_tool_schema`, `_make_tool_handler`, `_execute_tool`, `_execute_tool_safe`, `_fetch_tools`, `sync_tools`, `get_registered_tool_count`. Add `_registered_names` dict for name collision detection, `_stale_tool_ids` set for stale tracking. `_execute_tool` now accepts optional `user_id` and injects env vars via `fetch_user_env_vars`. `_execute_tool_safe` now distinguishes timeout, 4xx, 5xx errors. `sync_tools` detects name collisions (same name, different ID = skip + log error) and tracks removed tools as stale.

- [ ] **Step 2: Update test_handlers.py imports** -- Change `from kiln_mcp.main import _build_mcp_tool_schema, _make_tool_handler` to `from kiln_mcp.tools import _build_mcp_tool_schema, _make_tool_handler`

- [ ] **Step 3: Strip extracted code from main.py** -- Remove all tool-related functions, `_poll_task`, `load_tools_on_startup`, `_fetch_tools_sync`. Import from `kiln_mcp.tools` instead. Built-in tools remain.

- [ ] **Step 4: Run all existing tests** -- `uv run pytest packages/mcp_server/tests/ -v` -- all PASS

- [ ] **Step 5: Commit** -- `git commit -m "Extract tools module, fix stale tracking and name collisions"`

---

### Task 4: Create the OAuth provider

**Files:**
- Create: `packages/mcp_server/kiln_mcp/auth/provider.py`
- Create: `packages/mcp_server/tests/test_provider.py`

- [ ] **Step 1: Write the failing tests** -- 9 tests: register_client + get_client, get unknown returns None, authorize returns Clerk redirect URL, exchange_authorization_code returns tokens, load_access_token, expired token returns None, exchange_refresh_token rotates both, revoke_token deletes.

- [ ] **Step 2: Run tests to verify they fail** -- `uv run pytest packages/mcp_server/tests/test_provider.py -v` -- expected: ModuleNotFoundError

- [ ] **Step 3: Write the implementation** -- `KilnOAuthProvider` implementing all OAuthAuthorizationServerProvider methods. Extended models: `KilnAuthorizationCode(AuthorizationCode)` with `user_id`, `KilnAccessToken(AccessToken)` with `user_id`, `KilnRefreshToken(RefreshToken)` with `user_id`. `authorize()` encodes OAuth state as base64 JSON, redirects to `https://{clerk_domain}/sign-in?redirect_url={issuer}/oauth/callback?state={encoded}`. `exchange_authorization_code()` generates 32-byte urlsafe tokens, stores with TTLs (access=1h, refresh=30d), deletes auth code. `exchange_refresh_token()` rotates both tokens. `revoke_token()` deletes from store.

- [ ] **Step 4: Run tests to verify they pass** -- `uv run pytest packages/mcp_server/tests/test_provider.py -v` -- all 9 PASS

- [ ] **Step 5: Commit** -- `git commit -m "Add OAuth provider with Clerk redirect and token management"`

---

### Task 5: Create the Clerk callback route

**Files:**
- Create: `packages/mcp_server/kiln_mcp/auth/clerk_callback.py`
- Create: `packages/mcp_server/tests/test_clerk_callback.py`

- [ ] **Step 1: Write the failing tests** -- 3 tests using Starlette TestClient: missing state returns 400, invalid state returns 400, missing Clerk session returns 401.

- [ ] **Step 2: Run tests to verify they fail** -- `uv run pytest packages/mcp_server/tests/test_clerk_callback.py -v` -- expected: ModuleNotFoundError

- [ ] **Step 3: Write the implementation** -- `build_callback_route()` returns a Starlette Route for `GET /oauth/callback`. Decodes base64 state, extracts Clerk session from `__clerk_ticket` query param or `__session` cookie, verifies JWT via Clerk JWKS (reusing same pattern as `kiln_shared/auth.py`), generates auth code, stores in InMemoryOAuthStore, redirects to client's redirect_uri with code + state.

- [ ] **Step 4: Run tests to verify they pass** -- `uv run pytest packages/mcp_server/tests/test_clerk_callback.py -v` -- all 3 PASS

- [ ] **Step 5: Commit** -- `git commit -m "Add Clerk callback route for OAuth authorization flow"`

---

### Task 6: Wire OAuth into main.py and add lifespan

**Files:**
- Modify: `packages/mcp_server/kiln_mcp/main.py`
- Modify: `packages/mcp_server/tests/test_mcp_health.py`

- [ ] **Step 1: Rewrite main.py** -- Create `InMemoryOAuthStore` and `KilnOAuthProvider` at module level. If `CLERK_DOMAIN` and `CLERK_SECRET_KEY` are set, create FastMCP with `auth_server_provider`, `token_verifier=ProviderTokenVerifier(provider)`, and `AuthSettings` with `ClientRegistrationOptions(enabled=True, valid_scopes=["kiln:tools"], default_scopes=["kiln:tools"])`. Otherwise create unauthenticated FastMCP (with warning log). `build_http_app()` adds Clerk callback route alongside health routes. Lifespan: initial `sync_tools(mcp)` + `_poll_registry` task + `_cleanup_loop` task (60s interval). Health checks include `auth: enabled/disabled`.

- [ ] **Step 2: Run health tests** -- `uv run pytest packages/mcp_server/tests/test_mcp_health.py -v` -- all PASS

- [ ] **Step 3: Run all MCP tests** -- `uv run pytest packages/mcp_server/tests/ -v` -- all PASS

- [ ] **Step 4: Commit** -- `git commit -m "Wire OAuth provider into FastMCP, add lifespan for polling and cleanup"`

---

### Task 7: Update docker-compose and env config

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add Clerk env vars to mcp_server** -- Under mcp_server service environment, add: `CLERK_DOMAIN=${CLERK_DOMAIN:-}`, `CLERK_SECRET_KEY=${CLERK_SECRET_KEY:-}`, `KILN_MCP_ISSUER_URL=http://localhost:8768`

- [ ] **Step 2: Commit** -- `git commit -m "Add Clerk env vars to mcp_server in docker-compose"`

---

### Task 8: Run full test suite and verify

- [ ] **Step 1: Run all MCP server tests** -- `uv run pytest packages/mcp_server/tests/ -v` -- all PASS
- [ ] **Step 2: Run linter** -- `uv run ruff check packages/mcp_server/` -- no errors
- [ ] **Step 3: Run type checker** -- `uv run mypy packages/mcp_server/ --ignore-missing-imports` -- no errors
- [ ] **Step 4: Verify Docker build** -- `docker compose build mcp_server` -- succeeds
- [ ] **Step 5: Push branch** -- `git push`
