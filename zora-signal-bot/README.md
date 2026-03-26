# Zora Signal Bot

An event-driven Telegram bot that monitors X/Twitter accounts, detects virality-to-Zora conversion opportunities, scores them deterministically (with optional LLM assist), and sends structured alerts with inline approval buttons.

**Safe by default**: paper trading on, live trading off, every trade gated behind operator approval.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     Telegram Operator                        │
│                  (commands + inline buttons)                 │
└──────────────────────┬───────────────────────────────────────┘
                       │  python-telegram-bot
┌──────────────────────▼───────────────────────────────────────┐
│                  FastAPI  app/main.py                        │
│          health · readiness · /webhook endpoint              │
└──────┬──────────────────────────────────────┬───────────────┘
       │  SQLAlchemy async                    │  Redis
┌──────▼──────────┐                  ┌────────▼────────────────┐
│   PostgreSQL     │                  │  Celery Worker + Beat   │
│  (all state)    │                  │  (polling · scoring ·   │
└─────────────────┘                  │   alerts · settlement)  │
                                     └────────────┬────────────┘
                          ┌──────────────────────┬┴──────────────┐
                     ┌────▼────┐          ┌───────▼───┐  ┌───────▼───┐
                     │ X API   │          │ Zora API  │  │ LLM API   │
                     │(Phase 2)│          │ (Phase 2) │  │(optional) │
                     └─────────┘          └───────────┘  └───────────┘
```

**Design decisions:**
- **Deterministic-first scoring** — LLM is an optional annotation layer, never the sole decision-maker.
- **Three live-trading gates** — `LIVE_TRADING_ENABLED=true` in config AND `/live_on` at runtime AND `/approve <id>` per signal.
- **Every decision is logged** — signals with IGNORE recommendations are persisted too, enabling backtesting.
- **Webhook vs polling** — set `TELEGRAM_WEBHOOK_URL` for production; omit for long-polling in dev.

---

## Repository Layout

```
zora-signal-bot/
├── app/
│   ├── main.py                   FastAPI app, lifespan, health + webhook endpoints
│   ├── config.py                 Pydantic Settings — all config from environment
│   ├── logging_config.py         structlog: JSON in prod, pretty in dev
│   ├── bot/
│   │   ├── application.py        PTB Application factory + handler registration
│   │   ├── middleware.py         Admin auth helper
│   │   ├── renderer.py           Message formatting (pure functions, no side effects)
│   │   └── handlers/
│   │       ├── commands.py       All /command handlers (20 commands)
│   │       └── callbacks.py      Inline button callback dispatcher
│   ├── db/
│   │   ├── base.py               Async engine, session factory, Base class
│   │   ├── models.py             15 ORM models covering all domain tables
│   │   └── repositories/
│   │       └── base.py           Generic typed async CRUD base repository
│   ├── integrations/             Phase 2: x_client, zora_client, llm_client
│   ├── scoring/                  Phase 2: feature engineering + score calculator
│   ├── trading/                  Phase 3: paper engine, risk manager, exec adapter
│   └── jobs/
│       ├── celery_app.py         Celery app factory + queue config + beat schedule
│       └── tasks/
│           ├── ingestion.py      X post polling tasks (Phase 2)
│           ├── scoring.py        Signal scoring tasks (Phase 2/3)
│           ├── alerts.py         Telegram alert delivery (Phase 3)
│           └── settlement.py     Position monitoring / exit (Phase 3)
├── migrations/
│   ├── env.py                    Alembic async migration environment
│   └── script.py.mako            Migration file template
├── tests/
│   ├── conftest.py               Shared fixtures: in-memory SQLite, patched client
│   ├── unit/
│   │   ├── test_config.py        Settings validation + safety guard tests
│   │   ├── test_models.py        ORM model instantiation + enum values
│   │   ├── test_renderer.py      Message formatting correctness
│   │   └── test_bot_commands.py  Command handler logic (no real Telegram needed)
│   └── integration/
│       └── test_health.py        FastAPI endpoint tests via HTTPX AsyncClient
├── scripts/
│   └── init_db.py                Dev-only: create tables directly (skip Alembic)
├── Dockerfile                    Multi-stage build (builder + lean runtime)
├── docker-compose.yml            Full local stack: db, redis, api, worker, beat
├── alembic.ini
├── pyproject.toml                Dependencies + ruff/mypy/pytest config
└── .env.example                  All required environment variables
```

---

## Environment Variables

Copy `.env.example` to `.env`. Required variables are marked with ✅.

| Variable | Req | Default | Description |
|---|---|---|---|
| `APP_SECRET_KEY` | ✅ | — | Random 64-char signing secret |
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Token from @BotFather |
| `TELEGRAM_ADMIN_USER_IDS` | ✅ | — | Comma-separated Telegram user IDs |
| `DATABASE_URL` | ✅ | — | `postgresql+asyncpg://user:pass@host/db` |
| `APP_ENV` | | `development` | `development` / `staging` / `production` |
| `APP_LOG_LEVEL` | | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `TELEGRAM_WEBHOOK_URL` | | *(polling)* | Set for production webhook mode |
| `TELEGRAM_WEBHOOK_SECRET` | | *(polling)* | Random path token for webhook |
| `REDIS_URL` | | `redis://redis:6379/0` | |
| `X_BEARER_TOKEN` | Phase 2 | — | X API v2 bearer token |
| `X_API_KEY` / `X_API_SECRET` | Phase 2 | — | X API v1.1 keys |
| `ZORA_API_BASE_URL` | Phase 2 | `https://api.zora.co` | |
| `ZORA_API_KEY` | Phase 2 | — | |
| `LLM_ENABLED` | | `false` | Enable LLM classification layer |
| `OPENAI_API_KEY` | LLM | — | Required if `LLM_PROVIDER=openai` |
| `ANTHROPIC_API_KEY` | LLM | — | Required if `LLM_PROVIDER=anthropic` |
| `PAPER_TRADING_ENABLED` | | `true` | Synthetic trading on by default |
| `LIVE_TRADING_ENABLED` | | `false` | **Must stay false in development** |
| `LIVE_TRADING_REQUIRE_APPROVAL` | | `true` | Per-signal operator approval |
| `MAX_POSITION_SIZE_USD` | | `100.0` | Per-trade cap |
| `MAX_DAILY_LOSS_USD` | | `500.0` | Daily loss circuit breaker |
| `MAX_CONCURRENT_POSITIONS` | | `5` | |
| `MIN_LIQUIDITY_USD` | | `10000.0` | Reject coin if below this |
| `MAX_SLIPPAGE_BPS` | | `200` | 2% slippage cap |
| `NO_TRADE_AFTER_LAUNCH_SECONDS` | | `300` | Lockout window after coin launch |
| `SCORE_IGNORE_THRESHOLD` | | `30` | Below → IGNORE |
| `SCORE_WATCH_THRESHOLD` | | `50` | Below → WATCH |
| `SCORE_ALERT_THRESHOLD` | | `65` | Below → ALERT |
| `SCORE_PAPER_TRADE_THRESHOLD` | | `75` | Below → PAPER_TRADE |
| `SCORE_LIVE_TRADE_THRESHOLD` | | `85` | Above → LIVE_TRADE_READY |
| `WALLET_PRIVATE_KEY` | Live only | — | Never commit. Runtime inject only. |
| `WALLET_ADDRESS` | Live only | — | Checksummed Base address |

---

## Database Schema (Phase 1)

| Model | Table | Purpose |
|---|---|---|
| `BotUser` | `bot_users` | Telegram users who have interacted |
| `MonitoredAccount` | `monitored_accounts` | X accounts under active monitoring |
| `Creator` | `creators` | Zora creator profiles linked to X |
| `ZoraCoin` | `zora_coins` | Creator/content coins on Base |
| `Post` | `posts` | Ingested X posts |
| `PostMetricsSnapshot` | `post_metrics_snapshots` | Point-in-time engagement (for velocity) |
| `CoinMarketSnapshot` | `coin_market_snapshots` | Price / liquidity / volume snapshots |
| `Signal` | `signals` | Every scored opportunity (all recommendations) |
| `PaperPosition` | `paper_positions` | Synthetic trades with P&L tracking |
| `LivePosition` | `live_positions` | Real on-chain positions (feature-gated) |
| `RiskEvent` | `risk_events` | Safety rule violations (audit trail) |
| `CommandAuditLog` | `command_audit_log` | Every Telegram command invocation |

---

## Telegram Commands

| Command | Auth | Status | Description |
|---|---|---|---|
| `/start` | Public | ✅ | Welcome message |
| `/help` | Public | ✅ | Full command reference |
| `/health` | Public | ✅ | Liveness ping |
| `/status` | Admin | ✅ | Trading state + position counts |
| `/config` | Admin | ✅ | Show live configuration |
| `/kill` | Admin | ✅ | Emergency stop — halt all trading |
| `/watchlist` | Admin | ✅ | List monitored X accounts |
| `/addaccount @h` | Admin | Stub | Add X account (Phase 2) |
| `/removeaccount @h` | Admin | Stub | Remove X account (Phase 2) |
| `/score <url>` | Admin | Stub | Score a specific post (Phase 2/3) |
| `/recent` | Admin | Stub | Recent ingested posts (Phase 2) |
| `/signals` | Admin | Stub | Recent signals (Phase 3) |
| `/positions` | Admin | Stub | Open positions (Phase 3) |
| `/pnl` | Admin | Stub | Paper trading P&L (Phase 3) |
| `/paper_on` / `/paper_off` | Admin | ✅ | Toggle paper trading |
| `/live_on` / `/live_off` | Admin | ✅ | Toggle live trading (config-gated) |
| `/approve <id>` | Admin | Stub | Approve a signal (Phase 3/4) |
| `/reject <id>` | Admin | Stub | Reject a signal (Phase 3/4) |

---

## Running Locally

### Quick start with Docker Compose

```bash
git clone <repo> && cd zora-signal-bot
cp .env.example .env
# Edit .env — set APP_SECRET_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_USER_IDS

docker compose up --build
```

Services started in dependency order:
1. `db` (PostgreSQL 16) + `redis` (Redis 7)
2. `migrate` (Alembic `upgrade head`, exits cleanly)
3. `api` (FastAPI + Telegram bot in polling mode)
4. `worker` (Celery worker, queues: default, signals, alerts)
5. `beat` (Celery beat scheduler)

### Verify it's running

```bash
# Liveness
curl http://localhost:8000/health
# → {"status":"ok","uptime_seconds":4.2,"env":"development"}

# Readiness (checks DB + Redis + Telegram)
curl http://localhost:8000/ready

# Metrics
curl http://localhost:8000/metrics
```

### Run without Docker (dev)

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pip install aiosqlite   # async SQLite driver for tests

# Start only DB + Redis via Docker
docker compose up db redis -d

# Apply migrations
alembic upgrade head

# Start FastAPI in dev mode (long-polling, hot-reload)
uvicorn app.main:app --reload --port 8000
```

---

## Running Tests

No external services needed — tests use in-memory SQLite and mock the Telegram bot.

```bash
# All tests with coverage
pytest

# Unit tests only
pytest tests/unit/ -v

# Integration tests only
pytest tests/integration/ -v

# Single file
pytest tests/unit/test_renderer.py -v

# Show coverage report
pytest --cov=app --cov-report=html
open htmlcov/index.html
```

### Test coverage areas (Phase 1)

| File | What's tested |
|---|---|
| `test_config.py` | Settings parsing, admin ID resolution, live-trading safety guard, threshold ordering |
| `test_models.py` | ORM model creation, defaults, relationships, enum completeness |
| `test_renderer.py` | Signal alert formatting, age strings, truncation, keyboard buttons |
| `test_bot_commands.py` | Admin auth gate, kill switch, trading toggles, config display |
| `test_health.py` | `/health`, `/metrics`, 404 for unknown routes, webhook secret validation |

---

## Migrations

```bash
# After editing app/db/models.py — auto-generate migration
alembic revision --autogenerate -m "describe_your_change"

# Apply all pending migrations
alembic upgrade head

# Roll back one step
alembic downgrade -1

# Show migration history
alembic history --verbose

# Show current DB revision
alembic current
```

---

## Safety Architecture

```
Opportunity detected
        │
        ▼
  Kill switch?  ──YES──► DROP  (RiskEvent: KILL_SWITCH)
        │ NO
        ▼
  Liquidity OK? ──NO───► DROP  (RiskEvent: LOW_LIQUIDITY)
        │ YES
        ▼
  Slippage OK?  ──NO───► DROP  (RiskEvent: HIGH_SLIPPAGE)
        │ YES
        ▼
  Coin cooldown?──YES──► DROP  (RiskEvent: COIN_COOLDOWN)
        │ NO
        ▼
  Score ≥ threshold?
        │
        ├── < IGNORE_THRESHOLD  ──► Signal(IGNORE)  — logged, no action
        ├── < WATCH_THRESHOLD   ──► Signal(WATCH)   — logged, no alert
        ├── < ALERT_THRESHOLD   ──► Signal(ALERT)   — Telegram alert sent
        ├── < PAPER_THRESHOLD   ──► Signal(PAPER_TRADE) — auto paper trade
        │
        └── ≥ LIVE_THRESHOLD    ──► Signal(LIVE_TRADE_READY)
                                        Gate 1: LIVE_TRADING_ENABLED=true (config)
                                        Gate 2: /live_on (runtime toggle)
                                        Gate 3: /approve <signal_id> (operator)
                                        Gate 4: Balance + slippage re-check at tx time
```

---

## Phase Roadmap

| Phase | Status | Key deliverables |
|---|---|---|
| **1 — Foundation** | ✅ Done | Project structure · config · Docker · 15 DB models · 20 Telegram commands · health endpoints · tests |
| **2 — Ingestion** | ✅ Done | X API v2 client · Zora adapter + stub · scoring engine · feature extraction · Celery polling |
| **3 — Signals & Paper Trading** | ✅ Done | Signal pipeline · Telegram alerts with buttons · paper engine (SL/TP/timeout) · PnL summary · settlement job |
| **4 — LLM & Approvals** | ✅ Done | OpenAI + Anthropic LLM clients · JSON extraction · blacklist/whitelist · runtime config · approval workflow |
| **5 — Live Trading & Docs** | ✅ Done | ZoraOnChainAdapter (dry-run + gated broadcast) · LivePositionManager · CI pipeline · DEPLOYMENT.md |

---

## Security Checklist

- [x] All secrets via environment variables — never hardcoded
- [x] `WALLET_PRIVATE_KEY` never logged, never passed to LLM, never in API response
- [x] Webhook validates path secret + `X-Telegram-Bot-Api-Secret-Token` header
- [x] Admin commands gated by `TELEGRAM_ADMIN_USER_IDS` — all others silently rejected
- [x] `LIVE_TRADING_ENABLED=true` raises `ValidationError` in `APP_ENV=development`
- [x] Live trading requires three independent opt-in gates
- [x] Kill switch reachable via single `/kill` command, disables all trading immediately
- [x] API docs (`/docs`, `/redoc`) disabled in production
- [x] Non-root user in Docker image
