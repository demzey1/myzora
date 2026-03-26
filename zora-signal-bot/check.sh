#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# check.sh — Live health and error checker for running Zora Signal Bot
# Usage: ./check.sh
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

GRN='\033[0;32m'
RED='\033[0;31m'
YEL='\033[1;33m'
BLU='\033[0;34m'
NC='\033[0m'

ERRORS=0

pass() { echo -e "${GRN}✓  $1${NC}"; }
fail() { echo -e "${RED}✗  $1${NC}"; ERRORS=$((ERRORS+1)); }
warn() { echo -e "${YEL}⚠  $1${NC}"; }
head() { echo -e "\n${BLU}── $1 ──────────────────────────────────${NC}"; }

# ── Docker service status ─────────────────────────────────────────────────────
head "Container Status"
for svc in db redis api worker beat; do
  state=$(docker compose ps --format json 2>/dev/null | python3 -c "
import json,sys
for line in sys.stdin:
    try:
        d=json.loads(line)
        if '$svc' in d.get('Service',''):
            print(d.get('State','?'))
            break
    except: pass
" 2>/dev/null || echo "unknown")
  if [[ "$state" == "running" ]]; then
    pass "$svc: running"
  else
    fail "$svc: $state"
  fi
done

# ── HTTP health endpoints ─────────────────────────────────────────────────────
head "HTTP Health"

check_endpoint() {
  local url="$1" label="$2" expected="$3"
  if response=$(curl -sf --max-time 5 "$url" 2>/dev/null); then
    if echo "$response" | python3 -c "import json,sys; d=json.load(sys.stdin); assert '$expected' in str(d)" 2>/dev/null; then
      pass "$label"
    else
      warn "$label — unexpected response: ${response:0:100}"
    fi
  else
    fail "$label — no response from $url"
  fi
}

check_endpoint "http://localhost:8000/health"  "/health"   "ok"
check_endpoint "http://localhost:8000/metrics" "/metrics"  "uptime"

# /ready checks DB + Redis + Telegram
echo -n "  Checking /ready (DB + Redis + Telegram)... "
if ready=$(curl -sf --max-time 10 "http://localhost:8000/ready" 2>/dev/null); then
  if echo "$ready" | python3 -c "import json,sys; d=json.load(sys.stdin); checks=d.get('checks',{}); [print(f'    {k}: {v}') for k,v in checks.items()]" 2>/dev/null; then
    if echo "$ready" | grep -q '"status": "ready"' 2>/dev/null || echo "$ready" | python3 -c "import json,sys; assert json.load(sys.stdin).get('status')=='ready'" 2>/dev/null; then
      pass "/ready — all dependencies up"
    else
      warn "/ready — some dependencies degraded (see above)"
    fi
  fi
else
  fail "/ready — no response"
fi

# ── Recent error logs ─────────────────────────────────────────────────────────
head "Recent Errors (last 5 min)"

check_logs() {
  local svc="$1"
  errors=$(docker compose logs --since 5m "$svc" 2>/dev/null | grep -iE "error|exception|traceback|critical" | grep -v "WARNING" | tail -5)
  if [ -n "$errors" ]; then
    warn "$svc has recent errors:"
    echo "$errors" | head -5 | sed 's/^/    /'
  else
    pass "$svc — no errors in last 5 min"
  fi
}

check_logs api
check_logs worker
check_logs beat

# ── Celery worker check ───────────────────────────────────────────────────────
head "Celery Workers"
worker_ping=$(docker compose exec -T worker celery -A app.jobs.celery_app inspect ping --timeout 5 2>/dev/null || echo "failed")
if echo "$worker_ping" | grep -q "pong"; then
  pass "Celery worker responding"
else
  warn "Celery worker ping failed (may still be starting)"
fi

# ── Database connectivity ─────────────────────────────────────────────────────
head "Database"
db_check=$(docker compose exec -T db psql -U zora -d zora_signal -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | grep -E "^\s+[0-9]" | tr -d ' ')
if [ -n "$db_check" ]; then
  pass "PostgreSQL: $db_check tables in schema"
else
  fail "PostgreSQL: could not query tables"
fi

# ── Migration status ──────────────────────────────────────────────────────────
head "Migrations"
mig_status=$(docker compose exec -T api alembic current 2>/dev/null || echo "unavailable")
if echo "$mig_status" | grep -q "0002"; then
  pass "Migrations: at 0002 (latest)"
elif echo "$mig_status" | grep -q "0001"; then
  warn "Migrations: at 0001 — run: docker compose exec api alembic upgrade head"
else
  warn "Migrations: status unknown — $mig_status"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BLU}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [ "$ERRORS" -eq 0 ]; then
  echo -e "${GRN}  All checks passed ✓${NC}"
  echo -e "${GRN}  Bot is healthy and running${NC}"
else
  echo -e "${RED}  $ERRORS check(s) failed — see details above${NC}"
fi
echo -e "${BLU}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Quick commands:"
echo "  docker compose logs -f api          # live API logs"
echo "  docker compose logs -f worker       # live task logs"
echo "  docker compose exec api alembic current   # migration status"
echo "  docker compose down && ./start.sh   # full restart"
echo ""
exit $ERRORS
