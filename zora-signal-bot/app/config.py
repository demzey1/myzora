"""
app/config.py
─────────────────────────────────────────────────────────────────────────────
Central, typed configuration loaded from environment variables.
All secrets must be supplied via environment — never hardcoded.

Usage:
    from app.config import settings
    print(settings.telegram_bot_token)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = False
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    app_secret_key: SecretStr = Field(..., description="Random 64-char secret for internal signing")

    # ── Server ───────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Telegram ─────────────────────────────────────────────────────────
    telegram_bot_token: SecretStr = Field(..., description="Token from @BotFather")
    telegram_webhook_url: str | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_admin_user_ids: list[int] = Field(
        default_factory=list,
        description="Comma-separated Telegram user IDs with admin access",
    )

    @field_validator("telegram_admin_user_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: object) -> list[int]:
        if isinstance(v, str):
            return [int(uid.strip()) for uid in v.split(",") if uid.strip()]
        if isinstance(v, list):
            return [int(uid) for uid in v]
        return []

    # ── PostgreSQL ────────────────────────────────────────────────────────
    database_url: SecretStr = Field(
        ...,
        description="Full async DSN: postgresql+asyncpg://user:pass@host:port/db",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # ── Redis ─────────────────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # ── X / Twitter API ───────────────────────────────────────────────────
    x_api_key: SecretStr | None = None
    x_api_secret: SecretStr | None = None
    x_bearer_token: SecretStr | None = None
    x_access_token: SecretStr | None = None
    x_access_secret: SecretStr | None = None
    x_poll_interval_seconds: int = 60

    # ── Zora ──────────────────────────────────────────────────────────────
    zora_api_base_url: str = "https://api.zora.co"  # TODO: verify final base URL
    zora_api_key: SecretStr | None = None
    base_rpc_url: str = "https://mainnet.base.org"

    # ── LLM ───────────────────────────────────────────────────────────────
    llm_enabled: bool = False
    llm_provider: Literal["openai", "anthropic"] = "openai"
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 10
    llm_max_retries: int = 2

    # ── OpenAI Responses API (Conversational) ───────────────────────────────
    enable_conversational_mode: bool = True
    openai_api_base: str = "https://api.openai.com/v1"
    openai_responses_model: str = "gpt-4o-mini"
    conversation_timeout_minutes: int = 30

    # ── Scoring Thresholds ─────────────────────────────────────────────────
    score_ignore_threshold: int = 30
    score_watch_threshold: int = 50
    score_alert_threshold: int = 65
    score_paper_trade_threshold: int = 75
    score_live_trade_threshold: int = 85

    # ── Trading Safety ─────────────────────────────────────────────────────
    paper_trading_enabled: bool = True
    live_trading_enabled: bool = False          # Hard default: OFF
    live_trading_require_approval: bool = True  # Always require manual approval
    max_position_size_usd: float = 100.0
    max_daily_loss_usd: float = 500.0
    max_concurrent_positions: int = 5
    min_liquidity_usd: float = 10_000.0
    max_slippage_bps: int = 200                 # 2%
    no_trade_after_launch_seconds: int = 300
    paper_trade_size_usd: float = 50.0

    # ── Wallet (live trading only) ─────────────────────────────────────────
    wallet_private_key: SecretStr | None = None
    wallet_address: str | None = None

    # ── Social provider ─────────────────────────────────────────────────────
    # "x_api" = official Twitter v2  |  "socialdata" = socialdata.tools
    social_provider: str = "socialdata"
    socialdata_api_key: SecretStr | None = None
    socialdata_base_url: str = "https://api.socialdata.tools"

    # ── Alchemy (Base RPC) ──────────────────────────────────────────────────
    alchemy_api_key: SecretStr | None = None

    # ── Wallet linking flow ─────────────────────────────────────────────────
    enable_wallet_linking: bool = True
    wallet_link_base_url: str = "http://localhost:8000"
    wallet_link_secret: SecretStr | None = None
    wallet_nonce_ttl_seconds: int = 300

    # ── Creator intent tracking ─────────────────────────────────────────────
    enable_llm_classification: bool = False
    default_creator_mode: str = "hybrid"          # creator_only | keyword_only | hybrid
    creator_poll_interval_seconds: int = 120

    # ── Zora discovery ──────────────────────────────────────────────────────
    zora_trending_limit: int = 20
    min_creator_relevance_score: int = 40

    # ── Premium subscription ─────────────────────────────────────────────────
    # Your receiving wallet address on Base (users pay here)
    premium_payment_address: str | None = None
    premium_price_usd: float = 9.99
    premium_subscription_days: int = 30

    # ── Anthropic API (for AI chat) ──────────────────────────────────────────
    anthropic_api_key: SecretStr | None = None

    # ── Derived helpers ────────────────────────────────────────────────────
    @model_validator(mode="after")
    def safety_checks(self) -> "Settings":
        if self.live_trading_enabled and self.app_env == "development":
            raise ValueError(
                "LIVE_TRADING_ENABLED cannot be true in development environment. "
                "Set APP_ENV=production or APP_ENV=staging and review all safety settings."
            )
        if self.live_trading_enabled and not self.wallet_private_key:
            raise ValueError(
                "WALLET_PRIVATE_KEY is required when LIVE_TRADING_ENABLED=true"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def use_webhook(self) -> bool:
        return bool(self.telegram_webhook_url)

    def is_admin(self, user_id: int) -> bool:
        """Return True if the given Telegram user ID is an authorised admin."""
        return user_id in self.telegram_admin_user_ids

    @property
    def base_rpc_url_resolved(self) -> str:
        """Return Alchemy RPC if key set, else fall back to base_rpc_url."""
        if self.alchemy_api_key:
            return (
                f"https://base-mainnet.g.alchemy.com/v2/"
                f"{self.alchemy_api_key.get_secret_value()}"
            )
        return self.base_rpc_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()  # type: ignore[call-arg]


# Convenience module-level alias
settings: Settings = get_settings()
