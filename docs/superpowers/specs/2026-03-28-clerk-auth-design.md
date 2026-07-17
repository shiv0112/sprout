# Clerk Auth Integration Design Spec

**Date:** 2026-03-28
**Phase:** 2 — Auth (builds on Phase 1 rebrand/restructure)
**Status:** Approved

## Context

Kiln's API endpoints are currently unprotected. This spec adds Clerk-based authentication to protect write operations while keeping read endpoints public for tool discovery.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Auth scope | Protect writes only | Reads stay public for adoption/discovery |
| Auth UI | Clerk embedded components | `<SignIn/>`, `<UserButton/>` — native feel, minimal code |
| Auth methods | JWT + API keys | Browser uses JWT, CLI/MCP uses API keys |
| User storage | Clerk-only | API keys in Clerk user metadata, no local user DB |
| Auth module location | `kiln_shared/auth.py` | Shared across all services, single source of truth |

## Architecture

```
Browser request:
  Authorization: Bearer <clerk-jwt>
    → kiln_shared.auth.require_auth
    → verify JWT against Clerk JWKS (cached)
    → return KilnUser

CLI/MCP request:
  X-API-Key: kiln_<user_id>_<random>
    → kiln_shared.auth.require_auth
    → extract user_id from key format
    → fetch user from Clerk, verify key matches metadata
    → return KilnUser

No auth header + protected route:
    → HTTP 401 Unauthorized
```

## New Files

### `packages/shared/kiln_shared/auth.py`

Provides FastAPI dependencies for auth:

```python
@dataclass
class KilnUser:
    user_id: str
    email: str
    name: str

async def require_auth(request: Request) -> KilnUser:
    """FastAPI dependency. Returns KilnUser or raises 401."""
    # 1. Check Authorization: Bearer <jwt>
    # 2. Fallback to X-API-Key: <key>
    # 3. Raise HTTPException(401) if neither valid

async def require_jwt_auth(request: Request) -> KilnUser:
    """FastAPI dependency. JWT only — no API key fallback.
    Used for auth management endpoints (API key generation)."""
```

**JWT verification:**
- Use `CLERK_DOMAIN` env var to construct JWKS URL: `https://<clerk-domain>/.well-known/jwks.json`
- Cache JWKS for 1 hour (in-memory)
- Verify JWT signature, expiry, issuer using `pyjwt[crypto]`
- Extract `sub` (user_id), `email`, `name` from claims

**API key verification:**
- Read `X-API-Key` header
- API key format: `kiln_<user_id>_<32-char-hex>` — user_id is embedded in the key
- Extract user_id from key, fetch user from Clerk Backend API: `GET /users/<user_id>`
- Compare key against `user.private_metadata.api_key`
- Cache successful lookups for 5 minutes (in-memory, keyed by API key hash)
- On cache miss or mismatch, raise 401

This approach is O(1) per request (single Clerk API call, cached) and avoids the need to scan all users.

**Cache invalidation:**
- `POST /auth/api-key/regenerate` clears the cache entry for the old key

**New dependencies for `kiln-shared`:** `pyjwt[crypto]`, `httpx`

### `packages/shared/kiln_shared/config.py`

Centralized config for auth-related settings:

```python
CLERK_DOMAIN: str           # from env, e.g. "your-app.clerk.accounts.dev"
CLERK_SECRET_KEY: str       # from env
KILN_INTERNAL_SECRET: str   # from env, for service-to-service auth
```

## Auth Endpoints (on registry_api)

### `POST /auth/api-key`
- Requires: `require_jwt_auth` (JWT only, no API key fallback)
- Generates: `kiln_<user_id>_<32-char-random-hex>` API key
- Stores: in Clerk user's `private_metadata.api_key` via Backend API (`PATCH /users/<user_id>/metadata`)
- Returns: `{"api_key": "kiln_...", "created_at": "..."}`
- If key already exists, returns existing key (unmasked)

### `GET /auth/api-key`
- Requires: `require_jwt_auth`
- Returns: `{"api_key": "kiln_...****", "created_at": "..."}` (masked, last 4 chars visible)
- Returns 404 if no key generated yet

### `POST /auth/api-key/regenerate`
- Requires: `require_jwt_auth`
- Generates: new key, overwrites old one in Clerk metadata
- Clears auth cache entry for old key
- Returns: `{"api_key": "kiln_...", "created_at": "..."}`

## Protected Routes

### registry_api

| Route | Auth |
|---|---|
| `GET /health` | public |
| `GET /audio` | public |
| `GET /tools` | public |
| `GET /tools/{id}` | public |
| `POST /tools/register` | `require_auth` |
| `POST /tools/{id}/execute` | `require_auth` |
| `POST /tools/{id}/test` | public (intentional — for community tool testing, side effects are tool-author's responsibility) |
| `DELETE /tools/{id}` | `require_auth` |
| `POST /synthesis/callback` | internal (validate `KILN_INTERNAL_SECRET` header) |
| `POST /auth/api-key` | `require_jwt_auth` |
| `GET /auth/api-key` | `require_jwt_auth` |
| `POST /auth/api-key/regenerate` | `require_jwt_auth` |

### chat_backend

| Route | Auth |
|---|---|
| `POST /kiln/start` | `require_auth` |
| `POST /kiln/execute/{run_id}` | `require_auth` |
| `GET /kiln/stream/{run_id}` | public (auth checked at /start) |

The `KilnUser` from auth is logged with the run for traceability but runs are not scoped per-user in this phase.

## Chat UI Changes

### Dependencies
- Add `@clerk/clerk-react` to `packages/chat_ui/package.json`

### Component Changes
- `main.tsx`: Wrap `<App/>` in `<ClerkProvider publishableKey={...}>`
- `App.tsx`:
  - Add `<SignedIn>` / `<SignedOut>` guards
  - Show `<SignIn/>` component when signed out
  - Show `<UserButton/>` in header when signed in
  - Use `useAuth()` hook to get JWT token via `getToken()`
  - Attach `Authorization: Bearer <token>` to all POST fetch calls only (GETs stay unauthenticated)

### Vite Proxy Update
Add `/auth` proxy rule to `packages/chat_ui/vite.config.ts`:
```typescript
proxy: {
  '/kiln':       'http://localhost:8765',
  '/tools':      'http://localhost:8766',
  '/health':     'http://localhost:8766',
  '/audio':      'http://localhost:8766',
  '/auth':       'http://localhost:8766',   // NEW
  '/synthesis':  'http://localhost:8002',
}
```

### Environment
- `VITE_CLERK_PUBLISHABLE_KEY` — exposed to frontend via Vite env

## Service-to-Service Auth

The synthesis callback (`POST /synthesis/callback`) is called by the synthesis_service, not by users. Protected by a shared secret:

- Synthesis service sends `X-Internal-Secret: <secret>` header with callback requests
- Registry API validates the header value matches its own `KILN_INTERNAL_SECRET` env var
- No Clerk involvement for internal calls

**Required changes to synthesis_service:**
- Add `KILN_INTERNAL_SECRET` as a direct env var (not prefixed with `KILN_SYNTHESIS_` to avoid awkward naming)
- Update `kiln_synthesis/callback.py` `notify_success()` and `notify_failure()` to include the `X-Internal-Secret` header
- Add `KILN_INTERNAL_SECRET` to `kiln_synthesis/config.py` Settings class with `env_prefix` override or as a standalone field

## CORS Note

In local dev, the Vite proxy forwards requests to backends on the same origin (localhost:5173), so CORS + credentials is not an issue. The existing `allow_origins=["*"]` is fine for local dev. In production (Cloud Run), origins should be tightened to the actual frontend domains.

## Environment Variables

| Variable | Where | Purpose |
|---|---|---|
| `VITE_CLERK_PUBLISHABLE_KEY` | chat_ui | Clerk frontend SDK |
| `CLERK_DOMAIN` | registry_api, chat_backend (via kiln_shared) | JWKS URL for JWT verification |
| `CLERK_SECRET_KEY` | registry_api, chat_backend (via kiln_shared) | Clerk Backend API calls |
| `KILN_INTERNAL_SECRET` | registry_api, synthesis_service | Service-to-service callback auth |

## Out of Scope

- **MCP Server auth** — `packages/mcp_server/` remains a placeholder. API key auth for MCP clients will be added when the MCP Server is built.
- **Per-user run scoping** — Runs are not scoped to users yet. Tracked for a future phase.
- **RBAC / namespaces** — User roles and tool namespaces are a future phase.
- **`GET /audio` path restriction** — The endpoint serves files by absolute path. Security hardening deferred.

## What Stays Unchanged

- All GET endpoints remain public
- Tool implementations, spec format, Mistral/AG2 integration
- SQLite registry (no user tables needed)
- Synthesis pipeline (except callback header addition)
- SSE streaming (auth checked at /kiln/start, not on stream)

## Success Criteria

1. `POST /tools/register` returns 401 without valid JWT or API key
2. `GET /tools` works without auth
3. Clerk `<SignIn/>` component renders in Chat UI
4. After login, `<UserButton/>` appears in header
5. `POST /auth/api-key` generates a key stored in Clerk metadata
6. API key in `X-API-Key` header authenticates successfully on protected routes
7. Synthesis callback validates `KILN_INTERNAL_SECRET`
8. `POST /auth/api-key/regenerate` invalidates old key immediately (cache cleared)
