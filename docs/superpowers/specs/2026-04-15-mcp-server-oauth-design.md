# MCP Server OAuth 2.1 + Production Hardening

**Date:** 2026-04-15
**Status:** Approved
**Branch:** `fix/skip-saved-env-vars` (work in progress)

## Problem

The Sprout MCP server currently has zero per-user authentication. Any MCP client
connects anonymously and tool executions use `X-Internal-Secret` — no user
identity, no access to saved env vars (API keys), no audit trail. Standard MCP
clients (Claude Desktop, ChatGPT, Cursor, VS Code Copilot) expect OAuth 2.1
with PKCE for authentication.

Additionally, the current codebase has several production-readiness bugs:
polling is never started, race conditions in tool registration, stale tools
accumulate forever, and tool name collisions silently overwrite handlers.

## Solution

Implement the MCP server as an **OAuth 2.1 Authorization Server** using the MCP
SDK's `OAuthAuthorizationServerProvider` interface, delegating user identity to
**Clerk** (the existing auth provider). Fix all audited production bugs.

## OAuth Flow

```
MCP Client          MCP Server (AS)         Clerk (Identity)
    |                     |                       |
    |-- GET /mcp -------->|                       |
    |<-- 401 -------------|                       |
    |                     |                       |
    |-- GET /.well-known/ |                       |
    |   oauth-authz-server|                       |
    |<-- metadata --------|                       |
    |                     |                       |
    |-- POST /register -->|                       |
    |<-- client_id -------|                       |
    |                     |                       |
    |-- GET /authorize -->|                       |
    |   (PKCE challenge)  |-- redirect ---------->|
    |                     |                       |-- user logs in
    |                     |<-- redirect + session -|
    |                     |   /oauth/callback      |
    |<-- redirect --------|                       |
    |   (auth code)       |                       |
    |                     |                       |
    |-- POST /token ----->|                       |
    |   (code + verifier) |                       |
    |<-- access_token ----|                       |
    |    refresh_token    |                       |
    |                     |                       |
    |-- MCP requests ---->|                       |
    |   (Bearer token)    |-- fetch env vars ---->|
    |                     |   (Clerk Backend API) |
    |                     |-- execute tool ------>| (Registry API)
    |<-- result ----------|                       |
```

## Provider Implementation

### Storage (in-memory, dict-based)

All stores use TTL-based expiry. A background task prunes expired entries every
60 seconds. This is sufficient for single-instance deployment; Redis can replace
the dicts later without changing the interface.

| Store | Key | TTL | Contents |
|-------|-----|-----|----------|
| `_clients` | `client_id` | None (permanent until server restart) | `OAuthClientInformationFull` |
| `_auth_codes` | `code` (32 bytes, url-safe) | 10 minutes | PKCE challenge, client_id, redirect_uri, user_id, scopes |
| `_access_tokens` | `token` (32 bytes, url-safe) | 1 hour | client_id, user_id, scopes |
| `_refresh_tokens` | `token` (32 bytes, url-safe) | 30 days | client_id, user_id, scopes |

### Extended Token Models

The SDK's `AccessToken` and `RefreshToken` are extended with a `user_id` field:

```python
class SproutAccessToken(AccessToken):
    user_id: str

class SproutRefreshToken(RefreshToken):
    user_id: str
```

### Provider Methods

**`register_client(client_info)`**
- Store client metadata in `_clients`
- Public clients only (no client_secret required) -- MCP clients are native apps
- Accept any redirect_uri

**`authorize(client, params)`**
- Serialize the OAuth params (state, code_challenge, redirect_uri, scopes,
  client_id) into an encrypted/signed state parameter
- Redirect to Clerk's hosted sign-in:
  `https://{CLERK_DOMAIN}/sign-in?redirect_url={MCP_SERVER}/oauth/callback?state={encoded_state}`

**Clerk callback route (`GET /oauth/callback`)**
- This is a custom Starlette route, not part of the provider interface
- The `authorize()` method redirects to Clerk's hosted sign-in page. After the
  user logs in, Clerk redirects back to this callback URL with a `ticket` param
  (Clerk sign-in token)
- Exchange the ticket for user identity via Clerk Backend API
  (`POST /v1/sign_ins` or verify the ticket JWT directly using Clerk JWKS)
- Decode the state parameter to recover original OAuth params
- Generate authorization code (32 bytes, `secrets.token_urlsafe`)
- Store code with PKCE challenge, client_id, redirect_uri, user_id
- Redirect to client's redirect_uri with `code` and `state`

**Note:** This avoids relying on Clerk's `__session` cookie, which only works
when the MCP server shares Clerk's domain. The ticket-based flow works
cross-domain because the token is passed as a URL parameter.

**`exchange_authorization_code(client, auth_code)`**
- PKCE verification is handled by the SDK before this method is called
- Generate access token + refresh token
- Embed `user_id` from the auth code into both tokens
- Delete the auth code (single use)
- Return `OAuthToken(access_token=..., refresh_token=..., expires_in=3600)`

**`load_access_token(token)`**
- Look up in `_access_tokens`, check expiry
- Return `SproutAccessToken` with user_id, scopes

**`exchange_refresh_token(client, refresh_token, scopes)`**
- Validate refresh token exists and hasn't expired
- Generate new access + refresh tokens (rotation)
- Delete old refresh token
- Return new `OAuthToken`

**`revoke_token(token)`**
- Delete from the appropriate store
- If access token, also delete corresponding refresh token (and vice versa)

### Scopes

Single scope for now: `sprout:tools` (grants access to all tool operations).
No fine-grained per-tool scopes -- can be added later without breaking changes.

## Tool Execution with User Context

Current flow (anonymous):
```python
resp = await client.post(
    f"{REGISTRY_URL}/tools/{tool_id}/execute",
    json={"args": args},
    headers={"X-Internal-Secret": ...},
)
```

New flow (authenticated):
```python
user_env_vars = await fetch_user_env_vars(user_id)

resp = await client.post(
    f"{REGISTRY_URL}/tools/{tool_id}/execute",
    json={"args": args, "env_vars": user_env_vars},
    headers={
        "X-Internal-Secret": ...,
        "X-Sprout-User-ID": user_id,
    },
)
```

The `user_id` is extracted from the verified `SproutAccessToken` attached to the
MCP request by the SDK's auth middleware. The env var fetch reuses the same
Clerk Backend API call that `chat_backend/_fetch_user_tool_env_vars` uses.

### Extracting user_id from MCP request context

The MCP SDK's `AuthContextMiddleware` stores the verified `AccessToken` on the
Starlette request scope. Tool handlers access it via the FastMCP `Context`.

For unauthenticated requests (if auth is optional), `user_id` is None and no
env vars are injected.

## Bug Fixes

### 1. Polling never starts (CRITICAL)

**Current:** `_poll_task` is declared but never assigned. `_poll_registry()` is
never scheduled.

**Fix:** Add a Starlette lifespan context manager that starts the poll task on
startup and cancels it on shutdown.

This replaces `load_tools_on_startup()` (sync) with an async equivalent inside
the lifespan, eliminating the race condition (bug #2) at the same time.

### 2. Race condition in tool registration (CRITICAL)

**Current:** `load_tools_on_startup()` modifies `_registered_tools` without
acquiring `_tool_lock`.

**Fix:** Remove `load_tools_on_startup()`. Initial tool load moves into the
lifespan which calls `sync_tools()` -- that already acquires the lock.

### 3. Stale tools never removed (HIGH)

**Current:** Tools removed from registry are logged but stay registered in
FastMCP forever.

**Fix:** Track stale tool IDs in a set. During tool handler execution, if a tool
is in the stale set, return an error message telling the client to refresh. Log
a warning with the count of stale tools on each poll cycle. Document that a
server restart is required for full cleanup (FastMCP limitation).

### 4. Tool name collisions (HIGH)

**Current:** Two tools with the same name silently overwrite each other in
FastMCP.

**Fix:** In `sync_tools()`, build a `name -> id` map. If a name collision is
detected (same name, different ID), log an error and skip the newer tool.

### 5. Error handling too broad (MEDIUM)

**Current:** `_execute_tool_safe()` catches all exceptions identically.

**Fix:** Distinguish:
- `httpx.TimeoutException` -> `"Tool execution timed out"`
- `httpx.HTTPStatusError` with 4xx -> propagate the detail (user error)
- `httpx.HTTPStatusError` with 5xx -> log and return generic server error
- Other exceptions -> log with `logger.exception`, return generic error

## File Structure

```
packages/mcp_server/sprout_mcp/
  main.py                  # Entry point: FastMCP config, Starlette app, health routes, lifespan
  auth/
    __init__.py
    provider.py            # SproutOAuthProvider(OAuthAuthorizationServerProvider)
    store.py               # InMemoryOAuthStore -- clients, codes, tokens with TTL
    clerk_callback.py      # GET /oauth/callback -- Clerk redirect handler
  tools.py                 # sync_tools(), _make_tool_handler(), _execute_tool(), stale tracking
  user_env.py              # fetch_user_env_vars(user_id) -- Clerk private_metadata lookup
```

### main.py responsibilities (after refactor)
- Create `FastMCP` with `auth_server_provider` and `auth` settings
- Build Starlette app: health routes + Clerk callback route + MCP transport
- Lifespan: start polling, initial tool sync
- CLI entry point

### auth/provider.py
- `SproutOAuthProvider` class implementing all 9 provider methods
- Uses `InMemoryOAuthStore` for persistence
- `authorize()` redirects to Clerk sign-in

### auth/store.py
- `InMemoryOAuthStore` with typed dicts for clients, auth codes, access tokens,
  refresh tokens
- TTL enforcement on read (lazy) + periodic cleanup task (active)
- Thread-safe via asyncio locks

### auth/clerk_callback.py
- Starlette route handler for `GET /oauth/callback`
- Exchanges Clerk sign-in ticket for user identity via Backend API
- Generates auth code, stores it, redirects to client

### tools.py
- All tool registration logic extracted from main.py
- `sync_tools()` with lock, name collision detection, stale tracking
- `_make_tool_handler()` -- dynamic handler creation
- `_execute_tool()` with user context (env vars, user_id header)

### user_env.py
- `fetch_user_env_vars(user_id: str) -> dict[str, str]`
- Calls Clerk Backend API: `GET /v1/users/{user_id}`
- Returns `private_metadata.tool_env_vars` or empty dict
- Cached with 5-minute TTL (same as API key cache in auth.py)

## Configuration

New environment variables:

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `CLERK_DOMAIN` | Yes | -- | Clerk domain for sign-in redirect |
| `CLERK_SECRET_KEY` | Yes | -- | Clerk Backend API key for user lookups |
| `SPROUT_MCP_ISSUER_URL` | No | `http://localhost:8768` | OAuth issuer URL (must match what clients see) |

Existing variables unchanged: `SPROUT_REGISTRY_URL`, `SPROUT_MCP_HOST`,
`SPROUT_MCP_PORT`, `SPROUT_MCP_POLL_INTERVAL`, `SPROUT_INTERNAL_SECRET`.

Docker-compose additions for `mcp_server` service:
```yaml
environment:
  - CLERK_DOMAIN=${CLERK_DOMAIN:-}
  - CLERK_SECRET_KEY=${CLERK_SECRET_KEY:-}
  - SPROUT_MCP_ISSUER_URL=http://localhost:8768
```

## Testing Strategy

### Unit tests (pytest, mocked dependencies)

- `test_provider.py` -- all 9 provider methods: register, authorize, code
  exchange, token refresh, revocation, expiry, PKCE, invalid inputs
- `test_store.py` -- TTL expiry, cleanup task, concurrent access
- `test_clerk_callback.py` -- valid Clerk session, expired session, missing
  state, invalid state
- `test_tools.py` -- sync_tools lock, name collision, stale detection, error
  handling variants (timeout, 4xx, 5xx)
- `test_user_env.py` -- Clerk API mock, cache hit/miss, user not found

### Integration tests

- Full OAuth flow: register -> authorize -> callback -> token exchange -> MCP
  request with bearer token -> tool execution with env vars injected
- Token refresh flow
- Expired token rejection
- Unauthenticated request -> 401

## Non-goals

- Redis/persistent token storage (in-memory is fine for single instance)
- Fine-grained per-tool scopes
- Rate limiting on OAuth endpoints (can add later via middleware)
- Multiple simultaneous Clerk sessions (one user = one set of tokens)
