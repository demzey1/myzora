# Zora Signal Bot — Deployment Guide

## Prerequisites

- A server with Docker Engine 24+ and Docker Compose v2
- A domain name pointing to your server (for Telegram webhook TLS)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID (get it from [@userinfobot](https://t.me/userinfobot))
- X (Twitter) API bearer token with at least Essential access
- (Optional) OpenAI or Anthropic API key for LLM scoring

---

## Step 1 — Clone and configure

```bash
git clone <your-repo> zora-signal-bot
cd zora-signal-bot

# Create the production env file
cp .env.example .env.prod
```

Edit `.env.prod` — at minimum set:

```bash
APP_ENV=production
APP_SECRET_KEY=<64-char random string>     # openssl rand -hex 32

TELEGRAM_BOT_TOKEN=<from @BotFather>
TELEGRAM_ADMIN_USER_IDS=<your Telegram user ID>
TELEGRAM_WEBHOOK_URL=https://yourdomain.com
TELEGRAM_WEBHOOK_SECRET=<random string>    # openssl rand -hex 16

POSTGRES_USER=zora
POSTGRES_PASSWORD=<strong password>
POSTGRES_DB=zora_signal
DATABASE_URL=postgresql+asyncpg://zora:<password>@db:5432/zora_signal

REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
REDIS_PASSWORD=<strong password>
CELERY_BROKER_URL=redis://:${REDIS_PASSWORD}@redis:6379/1
CELERY_RESULT_BACKEND=redis://:${REDIS_PASSWORD}@redis:6379/2

X_BEARER_TOKEN=<from Twitter Developer Portal>

# Leave LIVE_TRADING_ENABLED=false until you have tested thoroughly
LIVE_TRADING_ENABLED=false
PAPER_TRADING_ENABLED=true
```

---

## Step 2 — Build the image

```bash
docker build --target runtime -t zora-signal-bot:latest .
```

Or use the IMAGE_TAG override to pull from a registry:

```bash
export IMAGE_TAG=registry.example.com/zora-signal-bot:v1.0.0
```

---

## Step 3 — Set up nginx with TLS

Install certbot and obtain a certificate:

```bash
sudo apt install nginx certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

Copy the nginx config:

```bash
sudo cp nginx.conf /etc/nginx/sites-available/zora-signal-bot
sudo ln -s /etc/nginx/sites-available/zora-signal-bot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## Step 4 — Launch the stack

```bash
docker compose -f docker-compose.prod.yml up -d

# Verify all containers are healthy
docker compose -f docker-compose.prod.yml ps

# Check the API is reachable
curl https://yourdomain.com/health
```

Expected response: `{"status":"ok","uptime_seconds":...,"env":"production"}`

---

## Step 5 — Register the Telegram webhook

The bot registers the webhook automatically on startup if `TELEGRAM_WEBHOOK_URL`
is set. You can verify it was registered:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

Expected: `"url": "https://yourdomain.com/webhook/<secret>"`

---

## Step 6 — First operator setup

Open your bot in Telegram and run:

```
/start
/health       — verify all dependencies are green
/config       — review default settings
/watchlist    — should be empty
```

Add your first monitored account:

```
/addaccount @somezoracreator
```

Check the signal pipeline is running:

```
/status       — shows signal counts, open positions
```

---

## Monitoring

### Logs

```bash
# API logs
docker compose -f docker-compose.prod.yml logs -f api

# Worker logs (signal processing)
docker compose -f docker-compose.prod.yml logs -f worker

# Beat scheduler
docker compose -f docker-compose.prod.yml logs -f beat
```

### Health endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness — returns 200 if process is running |
| `GET /ready` | Readiness — checks DB + Redis + Telegram |
| `GET /metrics` | Lightweight metrics snapshot |

### Database

```bash
# Connect to Postgres
docker compose -f docker-compose.prod.yml exec db \
  psql -U zora -d zora_signal

# Check signal counts
SELECT recommendation, COUNT(*) FROM signals GROUP BY recommendation;

# Recent paper positions
SELECT * FROM paper_positions ORDER BY opened_at DESC LIMIT 10;
```

---

## Scaling

The API and worker services can be scaled independently:

```bash
# Scale workers (for faster signal processing)
docker compose -f docker-compose.prod.yml up -d --scale worker=3

# Note: only ONE beat instance should ever run
# The beat service has restart:always — do not scale it
```

---

## Backup

```bash
# Backup Postgres
docker compose -f docker-compose.prod.yml exec db \
  pg_dump -U zora zora_signal | gzip > backup_$(date +%Y%m%d).sql.gz

# Backup Redis (optional — volatile data only)
docker compose -f docker-compose.prod.yml exec redis \
  redis-cli -a "${REDIS_PASSWORD}" BGSAVE
```

---

## Upgrading

```bash
# Pull new image
docker pull registry.example.com/zora-signal-bot:v1.1.0
export IMAGE_TAG=registry.example.com/zora-signal-bot:v1.1.0

# Rolling restart (migrate runs first)
docker compose -f docker-compose.prod.yml up -d

# Migrations run automatically via the `migrate` service on each deploy
```

---

## Enabling Live Trading

> **Read every line of this section before proceeding.**

Live trading involves real funds. The bot includes multiple layers of protection
but cannot guarantee against losses from adverse market conditions.

### Requirements

1. `APP_ENV` must be `staging` or `production`
2. `WALLET_PRIVATE_KEY` must be set (handle with extreme care — see security notes)
3. `WALLET_ADDRESS` must match the private key
4. `BASE_RPC_URL` must point to a reliable Base mainnet RPC endpoint
5. The wallet must hold sufficient ETH for trades + gas

### Enable sequence

```bash
# 1. Set in .env.prod
LIVE_TRADING_ENABLED=true
LIVE_TRADING_REQUIRE_APPROVAL=true   # NEVER set to false
WALLET_PRIVATE_KEY=0x<your key>
WALLET_ADDRESS=0x<your address>

# 2. Restart
docker compose -f docker-compose.prod.yml up -d

# 3. In Telegram — enable live trading for this session
/live_on

# 4. ALL live trades still require manual approval via inline button or /approve
```

### Security notes for the wallet private key

- Never commit `WALLET_PRIVATE_KEY` to version control
- Never log it (the settings model uses `SecretStr`)
- Use a dedicated hot wallet with limited funds (not your main wallet)
- Set up a hardware wallet or MPC solution for larger amounts
- The key is read at signing time and never stored in memory beyond the signing call
- The LLM code path cannot import `live_execution.py`

---

## Kill switch

At any time, from Telegram:

```
/kill
```

This immediately:
- Sets `kill_switch=True` in bot_data
- Disables paper and live trading
- All subsequent signal callbacks return no-op

To resume, restart the bot:

```bash
docker compose -f docker-compose.prod.yml restart api worker
```

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Bot not responding | `docker logs api` — look for startup error |
| Webhook not receiving updates | `curl getWebhookInfo` — verify URL and secret |
| Signals not appearing | `docker logs worker` — check X API errors |
| DB connection refused | `docker compose ps db` — check health status |
| Redis connection refused | Check `REDIS_PASSWORD` matches in env and compose |
| High slippage blocks all trades | `/setconfig max_slippage_bps 300` to loosen |
