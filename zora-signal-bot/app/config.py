from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = False
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    app_secret_key: SecretStr = Field(..., description="Random 64-char secret for internal signing")

    host: str = "0.0.0.0"
    port: int = 8000

    telegram_bot_token: SecretStr = Field(..., description="Token from @BotFather")
    telegram_webhook_url: str | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_admin_user_ids: str = ""

    @property
    def admin_user_ids(self) -> list[int]:
        return [int(uid.strip()) for uid in self.telegram_admin_user_ids.split(",") if uid.strip()]

    database_url: SecretStr = Field(...)
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    x_api_key: SecretStr | None = None
    x_api_secret: SecretStr | None = None
    x_bearer_token: SecretStr | None = None
    x_access_token: SecretStr | None = None
    x_access_secret: SecretStr | None = None
    x_poll_interval_seconds: int = 60

    zora_api_base_url: str = "https://api.zora.co"
    zora_api_key: SecretStr = Field(...)
    base_rpc_url: str = "https://mainnet.base.org"

    llm_enabled: bool = False
    llm_provider: Literal["openai", "anthropic"] = "openai"
    openai_api_key: SecretStr = Field(...)
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 10
    llm_max_retries: int = 2

    enable_conversational_mode: bool = True
    openai_api_base: str = "https://api.openai.com/v1"
    openai_responses_model: str = "gpt-4o-mini"
    conversation_timeout_minutes: int = 30

    score_ignore_threshold: int = 30
    score_watch_threshold: int = 50
    score_alert_threshold: int = 65
    score_paper_trade_threshold: int = 75
    score_live_trade_threshold: int = 85

    paper_trading_enabled: bool = True
    live_trading_enabled: bool = False
    live_trading_require_approval: bool = True
    max_position_size_usd: float = 100.0
    max_daily_loss_usd: float = 500.0
    max_concurrent_positions: int = 5
    min_liquidity_usd: float = 10000.0
    max_slippage_bps: int = 200
    no_trade_after_launch_seconds: int = 300
    paper_trade_size_usd: float = 50.0

    risk_max_trade_size_usd: float = 100.0
    risk_max_concurrent_positions: int = 5
    risk_max_daily_loss_usd: float = 500.0
    risk_max_slippage_bps: int = 200
    risk_min_liquidity_usd: float = 10000.0
    risk_require_wallet_link: bool = True
    risk_require_trading_enabled: bool = True

    wallet_private_key: SecretStr | None = None
    wallet_address: str | None = None

    social_provider: str = "socialdata"
    socialdata_api_key: SecretStr = Field(...)
    socialdata_base_url: str = "https://api.socialdata.tools"

    alchemy_api_key: SecretStr = Field(...)

    enable_wallet_linking: bool = True
    wallet_link_base_url: str = "http://localhost:8000"
    wallet_link_secret: SecretStr | None = None
    wallet_nonce_ttl_seconds: int = 300

    enable_llm_classification: bool = False
    default_creator_mode: str = "hybrid"
    creator_poll_interval_seconds: int = 120

    zora_trending_limit: int = 20
    min_creator_relevance_score: int = 40

    premium_payment_address: str | None = None
    premium_price_usd: float = 9.99
    premium_subscription_days: int = 30

    @model_validator(mode="after")
    def safety_checks(self) -> "Settings":
        if self.telegram_webhook_url and not self.telegram_webhook_secret:
            raise ValueError("TELEGRAM_WEBHOOK_SECRET is required when TELEGRAM_WEBHOOK_URL is set")
        if self.enable_wallet_linking and not self.wallet_link_secret:
            raise ValueError("WALLET_LINK_SECRET is required when ENABLE_WALLET_LINKING=true")
        if self.live_trading_enabled and self.app_env == "development":
            raise ValueError(
                "LIVE_TRADING_ENABLED cannot be true in development environment. "
                "Set APP_ENV=production or APP_ENV=staging and review all safety settings."
            )
        if self.live_trading_enabled and not self.wallet_private_key:
            raise ValueError("WALLET_PRIVATE_KEY is required when LIVE_TRADING_ENABLED=true")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def use_webhook(self) -> bool:
        return bool(self.telegram_webhook_url)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_user_ids

    @property
    def base_rpc_url_resolved(self) -> str:
        if self.alchemy_api_key:
            return f"https://base-mainnet.g.alchemy.com/v2/{self.alchemy_api_key.get_secret_value()}"
        return self.base_rpc_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings: Settings = get_settings()
