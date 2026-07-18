# Deploying Sprout — droplet backend + Vercel frontend

The **frontend** (`packages/registry_ui`) deploys to **Vercel**. Everything else —
the Python services, PostgreSQL, Redis, and a Caddy reverse proxy — runs on **one
droplet** as a Docker Compose stack, deployed (and updated) with one command:
`./deploy.sh`.

```
        Browser ───────────────► Vercel (Next.js UI, HTTPS)
           │                         │  (SSR / /api route handlers)
           │  chat SSE, execute,     │
           │  tool-env-vars (direct) │
           ▼                         ▼
   ┌──────────────────────────────────────────┐
   │  Droplet — Caddy (HTTPS, 80/443)          │
   │   /registry/*  ─► registry_api            │
   │   /chat/*      ─► chat_backend  (SSE)     │
   │   /mcp/*       ─► mcp_server  ◄── MCP clients (Claude Desktop, …)
   │   (internal)   synthesis_service, tool_executor, postgres, redis
   └──────────────────────────────────────────┘
```

## How the droplet is restricted

There is no single "master secret" — a public web frontend means the browser
calls the backend directly (chat streaming, tool execution), and MCP clients
connect directly too, so any secret placed in the browser would be visible.
Instead the backend is locked down in layers:

1. **CORS locked to your Vercel origin** — `CORS_ORIGINS` is the main gate: only
   your Vercel domain's browser may call the API cross-origin. The backend
   *refuses to start* in production if it isn't set.
2. **Minimal public surface** — only `registry`, `chat`, and `mcp` are exposed.
   `synthesis_service` and `tool_executor` are reachable only over the internal
   Docker network (so nobody can trigger paid LLM synthesis or code execution
   from outside). Postgres and Redis are internal-only.
3. **Per-user auth** — sensitive endpoints require a Clerk JWT (browser) or an
   API key (CLI/MCP); MCP uses OAuth 2.1 + PKCE.
4. **Service-to-service secret** — `SPROUT_INTERNAL_SECRET` guards internal calls
   (e.g. the synthesis callback).
5. **Host firewall** — expose only 22/80/443 (below).

> Want maximum lockdown? You can route *all* browser traffic through Vercel's own
> `/api` handlers (a full BFF) and put a shared gateway secret between Vercel and
> the droplet. That needs frontend changes (the chat page currently streams from
> the droplet directly) — ask and I'll wire it.

## 1. One-time droplet setup

Size: **2 vCPU / 4 GB** minimum (6 services + Postgres + Redis). Ubuntu 22.04+.

```bash
curl -fsSL https://get.docker.com | sh                       # Docker + Compose v2
sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable
```

If you use a **DigitalOcean Cloud Firewall**, allow inbound only on `22`, `80`,
`443` and drop everything else.

### HTTPS is required — pick a hostname

Because Vercel is HTTPS, the droplet must be HTTPS too. Caddy gets a free
Let's Encrypt cert automatically, but it needs a **hostname** (a cert can't be
issued for a bare IP):

- **Have a domain?** Add an `A` record, e.g. `api.example.com → <droplet-ip>`.
- **Just an IP?** Use free [sslip.io](https://sslip.io): droplet `203.0.113.10`
  becomes `api.203-0-113-10.sslip.io` (resolves to that IP, no DNS setup).

## 2. Configure and deploy

```bash
git clone https://github.com/<your-org>/sprout.git && cd sprout
cp .env.prod.example .env.prod
nano .env.prod        # fill in — see the table below
./deploy.sh
```

Key values in `.env.prod`:

| Key | Value |
|-----|-------|
| `SITE_ADDRESS` | your backend hostname, e.g. `api.203-0-113-10.sslip.io` |
| `PUBLIC_BASE_URL` | `https://` + that hostname |
| `CORS_ORIGINS` | your Vercel URL, e.g. `https://your-app.vercel.app` (comma-separate extras) |
| `POSTGRES_PASSWORD` | a strong password |
| `SPROUT_INTERNAL_SECRET` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `NVIDIA_API_KEY` / `MISTRAL_API_KEY` | LLM providers |
| `CLERK_DOMAIN`, `CLERK_SECRET_KEY` | Clerk (backend verifies the JWT) |

`deploy.sh` builds the images, starts the stack, auto-creates the DB schema, and
provisions the TLS cert. Check `PUBLIC_BASE_URL/registry/health`.

## 3. Configure Vercel (frontend)

In the Vercel project → **Settings → Environment Variables**, point the UI at the
droplet (replace the host with your `PUBLIC_BASE_URL`):

```
NEXT_PUBLIC_REGISTRY_URL          = https://api.203-0-113-10.sslip.io/registry
NEXT_PUBLIC_CHAT_BACKEND          = https://api.203-0-113-10.sslip.io/chat
NEXT_PUBLIC_MCP_SERVER_URL        = https://api.203-0-113-10.sslip.io/mcp
NEXT_PUBLIC_SYNTHESIS_SERVICE_URL = https://api.203-0-113-10.sslip.io   # (health only)
NEXT_PUBLIC_TOOL_EXECUTOR_URL     = https://api.203-0-113-10.sslip.io   # (health only)

# server-side (SSR / route handlers) — same public URLs (no private network to Vercel)
REGISTRY_API_INTERNAL   = https://api.203-0-113-10.sslip.io/registry
CHAT_BACKEND_INTERNAL   = https://api.203-0-113-10.sslip.io/chat
MCP_SERVER_INTERNAL     = https://api.203-0-113-10.sslip.io/mcp

# Clerk (frontend)
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = pk_live_...
CLERK_SECRET_KEY                  = sk_live_...
```

Set the **Root Directory** to `packages/registry_ui` in Vercel's project
settings. Redeploy the Vercel project after changing env vars.

> `synthesis` and `tool_executor` aren't publicly routed, so the status panel may
> show those two as unreachable from the browser — that's expected (they're
> intentionally private). The core app is unaffected.

## Updating

```bash
git pull && ./deploy.sh     # rebuilds only what changed, then rolls the stack
```

## Handy commands

```bash
./deploy.sh ps       # status
./deploy.sh logs     # tail all logs
./deploy.sh down     # stop (named volumes / data are preserved)
```

## Notes

- MCP clients connect to `PUBLIC_BASE_URL/mcp`.
- Postgres data lives in the `pgdata` volume; it survives `down`/redeploys.
  Back up with `docker compose -f docker-compose.prod.yml exec postgres pg_dump ...`.
- Changing `PUBLIC_BASE_URL` later (e.g. sslip.io → real domain) means updating
  `.env.prod` **and** the Vercel env vars, then redeploying both.
