#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Sprout — one-command production deploy for a single host (e.g. a DO droplet).
#
#   ./deploy.sh          Build every image and (re)start the whole stack.
#   ./deploy.sh logs     Tail logs from all services.
#   ./deploy.sh ps       Show service status.
#   ./deploy.sh down     Stop the stack (data volumes are kept).
#
# To update after a `git pull`, just run ./deploy.sh again — changed images
# rebuild and roll, unchanged ones stay put.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE=".env.prod"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml)

case "${1:-up}" in
  logs) exec "${COMPOSE[@]}" logs -f --tail=100 ;;
  ps)   exec "${COMPOSE[@]}" ps ;;
  down) exec "${COMPOSE[@]}" down ;;
esac

# ── Preflight ────────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || { echo "✗ Docker is not installed. See docs/DEPLOY_DROPLET.md"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "✗ Docker Compose v2 is required (docker compose ...)."; exit 1; }

if [ ! -f "$ENV_FILE" ]; then
  echo "✗ $ENV_FILE not found."
  echo "  Run:  cp .env.prod.example $ENV_FILE   then fill in your keys."
  exit 1
fi

# Required keys must be present and not left as placeholders.
missing=0
require() {
  local key="$1" val
  val="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  if [ -z "$val" ] || echo "$val" | grep -qiE 'REPLACE_|change_me|your_|xxxx'; then
    echo "  ✗ $key is unset or still a placeholder in $ENV_FILE"
    missing=1
  fi
}
for k in SITE_ADDRESS PUBLIC_BASE_URL CORS_ORIGINS POSTGRES_PASSWORD SPROUT_INTERNAL_SECRET; do require "$k"; done
if [ "$missing" -ne 0 ]; then
  echo "Fill the values above in $ENV_FILE, then re-run ./deploy.sh"
  exit 1
fi

PUBLIC_BASE_URL="$(grep -E '^PUBLIC_BASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"

# ── Build + start ────────────────────────────────────────────────────────────
echo "▶ Building images and starting the Sprout backend…"
"${COMPOSE[@]}" up -d --build --remove-orphans

echo ""
"${COMPOSE[@]}" ps
echo ""
echo "✅ Sprout backend is deploying at:  ${PUBLIC_BASE_URL}"
echo "   • Registry:    ${PUBLIC_BASE_URL}/registry/health"
echo "   • Chat:        ${PUBLIC_BASE_URL}/chat"
echo "   • MCP server:  ${PUBLIC_BASE_URL}/mcp"
echo "   • Logs:        ./deploy.sh logs"
echo ""
echo "Point your Vercel frontend at these URLs (see docs/DEPLOY_DROPLET.md)."
echo "First boot builds images, initializes the DB, and provisions a TLS cert —"
echo "give it a minute, then check ${PUBLIC_BASE_URL}/registry/health"
