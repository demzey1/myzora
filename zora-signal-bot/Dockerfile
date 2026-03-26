# ─────────────────────────────────────────────────────────────────────────────
# Zora Signal Bot — Dockerfile
# Uses plain pip for maximum compatibility (no uv required)
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS runtime

# System deps needed for some Python packages (asyncpg, cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency manifest first (layer cache)
COPY pyproject.toml .

# Install all runtime + dev dependencies with pip
# We extract deps from pyproject.toml directly
RUN pip install --upgrade pip && \
    pip install --no-cache-dir \
        fastapi>=0.115.0 \
        "uvicorn[standard]>=0.30.0" \
        "python-telegram-bot[webhooks]>=21.6" \
        "sqlalchemy[asyncio]>=2.0.36" \
        asyncpg>=0.29.0 \
        alembic>=1.13.3 \
        "redis>=5.1.0" \
        "celery[redis]>=5.4.0" \
        "pydantic>=2.9.0" \
        "pydantic-settings>=2.5.0" \
        "httpx>=0.27.2" \
        "structlog>=24.4.0" \
        python-dateutil>=2.9.0 \
        "tenacity>=9.0.0" \
        "orjson>=3.10.7" \
        "eth-account>=0.11.0" \
        "eth-abi>=5.0.0" \
        gunicorn \
        aiosqlite \
        pytest pytest-asyncio pytest-cov respx

# Copy application source
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
