#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — Zora Signal Bot one-command launcher
# Usage: ./start.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GRN='\033[0;32m'
YEL='\033[1;33m'
BLU='\033[0;34m'
NC='\033[0m'

echo -e "${BLU}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLU}  Zora Signal Bot — Pre-flight checks${NC}"
echo -e "${BLU}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

ERRORS=0

# ── Check .env exists ─────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  echo -e "${RED}✗  .env not found — copy .env.example and fill in values${NC}"
  exit 1
fi
echo -e "${GRN}✓  .env found${NC}"

# ── Check required keys ───────────────────────────────────────────────────────
source .env 2>/dev/null || true

check_key() {
  local key="$1"
  local val="${!key:-}"
  if [ -z "$val" ] || [[ "$val" == *"PASTE_"* ]] || [[ "$val" == *"REPLACE_"* ]]; then
    echo -e "${RED}✗  $key is not set${NC}"
    ERRORS=$((ERRORS + 1))
  else
    echo -e "${GRN}✓  $key${NC}"
  fi
}

check_key "TELEGRAM_BOT_TOKEN"
check_key "TELEGRAM_ADMIN_USER_IDS"
check_key "SOCIALDATA_API_KEY"
check_key "ZORA_API_KEY"
check_key "ALCHEMY_API_KEY"

# ── Check Docker ──────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo -e "${RED}✗  Docker not found — install from https://docs.docker.com/get-docker/${NC}"
  ERRORS=$((ERRORS + 1))
else
  echo -e "${GRN}✓  Docker $(docker --version | cut -d' ' -f3 | tr -d ',')${NC}"
fi

if ! docker compose version &>/dev/null 2>&1; then
  echo -e "${RED}✗  Docker Compose v2 not found${NC}"
  ERRORS=$((ERRORS + 1))
else
  echo -e "${GRN}✓  Docker Compose$(NC)"
fi

if [ "$ERRORS" -gt 0 ]; then
  echo ""
  echo -e "${RED}━━━ $ERRORS pre-flight error(s) — fix above before starting ━━━${NC}"
  exit 1
fi

echo ""
echo -e "${BLU}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLU}  Starting all services...${NC}"
echo -e "${BLU}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Services: db → redis → migrate → api + worker + beat"
echo "Logs:     docker compose logs -f"
echo "Stop:     docker compose down"
echo ""

docker compose up --build -d

echo ""
echo -e "${YEL}Waiting for API to be healthy...${NC}"
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${GRN}✓  API is live at http://localhost:8000${NC}"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo -e "${RED}✗  API did not start in 60s — check: docker compose logs api${NC}"
    exit 1
  fi
  sleep 2
done

echo ""
echo -e "${GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GRN}  Bot is running!${NC}"
echo -e "${GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Health:   curl http://localhost:8000/health"
echo "  Readiness: curl http://localhost:8000/ready"
echo "  API docs: http://localhost:8000/docs"
echo ""
echo "  Telegram: send /start to your bot"
echo ""
echo "  Live logs:"
echo "    docker compose logs -f api       # bot + FastAPI"
echo "    docker compose logs -f worker    # Celery tasks"
echo "    docker compose logs -f beat      # Scheduler"
echo ""
echo "  Stop everything:"
echo "    docker compose down"
echo ""
