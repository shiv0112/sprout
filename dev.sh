#!/bin/bash
# Kiln — Start all services via Docker Compose
# Hot reload enabled via volume mounts
# Usage: ./dev.sh

set -e
cd "$(dirname "$0")"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

if [ ! -f .env ]; then
  echo "⚠  No .env file. Copy .env.example and add your keys:"
  echo "   cp .env.example .env"
  exit 1
fi

# Export Clerk keys for docker-compose interpolation
set -a; source .env; set +a
export NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="${NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY:-$(grep VITE_CLERK packages/chat_ui/.env 2>/dev/null | cut -d= -f2)}"

echo -e "${GREEN}🔥 Kiln — Starting all services with Docker Compose${NC}"
echo ""

docker compose up --build "$@"
