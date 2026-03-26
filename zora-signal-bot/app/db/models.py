"""
app/db/models.py
─────────────────────────────────────────────────────────────────────────────
All SQLAlchemy ORM models.
Import this module in migrations/env.py so Alembic can auto-detect changes.

Model inventory:
  - BotUser               – Telegram users known to the bot
  - MonitoredAccount      – X/Twitter accounts being tracked
  - Creator               – Zora creator profiles linked to X accounts
  - ZoraCoin              – Zora coins (creator coins + content coins)
  - Post                  – Ingested X posts
  - PostMetricsSnapshot   – Point-in-time engagement snapshot per post
  - CoinMarketSnapshot    – Point-in-time market state per coin
  - Signal                – Scored opportunity produced by the engine
  - PaperPosition         – Synthetic trade (paper trading)
  - LivePosition          – Real on-chain position (gated by feature flag)
  - RiskEvent             – Risk rule violations / guardrail triggers
  - CommandAuditLog       – Every Telegram command invocation
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


# ── Enums ─────────────────────────────────────────────────────────────────────

class Recommendation(str, enum.Enum):
    IGNORE = "IGNORE"
    WATCH = "WATCH"
    ALERT = "ALERT"
    PAPER_TRADE = "PAPER_TRADE"
    LIVE_TRADE_READY = "LIVE_TRADE_READY"


class PositionStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"      # Stop-loss triggered
    EXPIRED = "EXPIRED"      # Timeout exit


class RiskEventType(str, enum.Enum):
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    HIGH_SLIPPAGE = "HIGH_SLIPPAGE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    CONCURRENT_POSITION_LIMIT = "CONCURRENT_POSITION_LIMIT"
    COIN_COOLDOWN = "COIN_COOLDOWN"
    NEW_COIN_LOCKOUT = "NEW_COIN_LOCKOUT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    BLACKLISTED = "BLACKLISTED"
    KILL_SWITCH = "KILL_SWITCH"
    MANUAL_REJECT = "MANUAL_REJECT"


# ── Mixins ────────────────────────────────────────────────────────────────────

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ── Models ────────────────────────────────────────────────────────────────────

class BotUser(TimestampMixin, Base):
    """Telegram user who has interacted with the bot."""

    __tablename__ = "bot_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Per-user overrides (null = use global setting)
    paper_trading_enabled: Mapped[bool | None] = mapped_column(Boolean)

    audit_logs: Mapped[list["CommandAuditLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class MonitoredAccount(TimestampMixin, Base):
    """X/Twitter account being actively monitored."""

    __tablename__ = "monitored_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    x_user_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    x_username: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    follower_count: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Linked Zora creator (if resolved)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"), nullable=True)

    creator: Mapped["Creator | None"] = relationship(back_populates="monitored_accounts")
    posts: Mapped[list["Post"]] = relationship(back_populates="account")


class Creator(TimestampMixin, Base):
    """Zora creator profile, potentially linked to an X account."""

    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Zora on-chain address (checksummed)
    wallet_address: Mapped[str] = mapped_column(String(42), unique=True, nullable=False)
    zora_profile_url: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(255))
    bio: Mapped[str | None] = mapped_column(Text)
    # X handle if known
    x_username: Mapped[str | None] = mapped_column(String(255), index=True)
    # Historical conversion rate: how often this creator's signals led to gains
    historical_conversion_rate: Mapped[float | None] = mapped_column(Float)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    monitored_accounts: Mapped[list["MonitoredAccount"]] = relationship(
        back_populates="creator"
    )
    coins: Mapped[list["ZoraCoin"]] = relationship(back_populates="creator")


class ZoraCoin(TimestampMixin, Base):
    """A Zora creator coin or content coin."""

    __tablename__ = "zora_coins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # On-chain contract address on Base
    contract_address: Mapped[str] = mapped_column(String(42), unique=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"), nullable=True)
    # Chain data
    chain_id: Mapped[int] = mapped_column(Integer, default=8453)  # Base mainnet
    decimals: Mapped[int] = mapped_column(Integer, default=18)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Cooldown tracking
    last_traded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    creator: Mapped["Creator | None"] = relationship(back_populates="coins")
    market_snapshots: Mapped[list["CoinMarketSnapshot"]] = relationship(
        back_populates="coin", cascade="all, delete-orphan"
    )
    signals: Mapped[list["Signal"]] = relationship(back_populates="coin")


class Post(TimestampMixin, Base):
    """An X/Twitter post ingested by the monitoring pipeline."""

    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("x_post_id", name="uq_posts_x_post_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    x_post_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("monitored_accounts.id"), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lang: Mapped[str | None] = mapped_column(String(8))
    # Raw metrics at ingest time
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    repost_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    quote_count: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int | None] = mapped_column(Integer)
    # Processing state
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    zora_coin_id: Mapped[int | None] = mapped_column(ForeignKey("zora_coins.id"), nullable=True)

    account: Mapped["MonitoredAccount"] = relationship(back_populates="posts")
    metrics_snapshots: Mapped[list["PostMetricsSnapshot"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )
    signals: Mapped[list["Signal"]] = relationship(back_populates="post")


class PostMetricsSnapshot(Base):
    """Point-in-time engagement snapshot for a post (for velocity calculation)."""

    __tablename__ = "post_metrics_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    repost_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    quote_count: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int | None] = mapped_column(Integer)

    post: Mapped["Post"] = relationship(back_populates="metrics_snapshots")


class CoinMarketSnapshot(Base):
    """Point-in-time market state for a Zora coin."""

    __tablename__ = "coin_market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coin_id: Mapped[int] = mapped_column(ForeignKey("zora_coins.id"), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    price_usd: Mapped[float | None] = mapped_column(Float)
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    volume_5m_usd: Mapped[float | None] = mapped_column(Float)
    volume_1h_usd: Mapped[float | None] = mapped_column(Float)
    volume_24h_usd: Mapped[float | None] = mapped_column(Float)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    holder_count: Mapped[int | None] = mapped_column(Integer)
    # Slippage estimate for a reference trade size
    slippage_bps_reference: Mapped[int | None] = mapped_column(Integer)

    coin: Mapped["ZoraCoin"] = relationship(back_populates="market_snapshots")


class Signal(TimestampMixin, Base):
    """
    A scored opportunity produced by the signal engine.
    Every decision (including IGNORE) is persisted for auditability.
    """

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id"), nullable=True)
    coin_id: Mapped[int | None] = mapped_column(ForeignKey("zora_coins.id"), nullable=True)

    # Score breakdown
    deterministic_score: Mapped[float] = mapped_column(Float, nullable=False)
    llm_score: Mapped[float | None] = mapped_column(Float)
    final_score: Mapped[float] = mapped_column(Float, nullable=False)

    # LLM fields (populated only when LLM is enabled)
    llm_meme_strength: Mapped[int | None] = mapped_column(Integer)
    llm_narrative_fit: Mapped[int | None] = mapped_column(Integer)
    llm_conversion_likelihood: Mapped[int | None] = mapped_column(Integer)
    llm_spam_risk: Mapped[int | None] = mapped_column(Integer)
    llm_summary: Mapped[str | None] = mapped_column(Text)
    llm_recommendation_bias: Mapped[str | None] = mapped_column(String(16))

    recommendation: Mapped[Recommendation] = mapped_column(
        Enum(Recommendation), nullable=False, index=True
    )
    risk_notes: Mapped[str | None] = mapped_column(Text)
    # Telegram message ID of the alert sent (for inline button callbacks)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    # Operator decisions
    is_approved: Mapped[bool | None] = mapped_column(Boolean)
    approved_by: Mapped[int | None] = mapped_column(BigInteger)  # Telegram user ID
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    post: Mapped["Post | None"] = relationship(back_populates="signals")
    coin: Mapped["ZoraCoin | None"] = relationship(back_populates="signals")
    paper_positions: Mapped[list["PaperPosition"]] = relationship(back_populates="signal")
    live_positions: Mapped[list["LivePosition"]] = relationship(back_populates="signal")


class PaperPosition(TimestampMixin, Base):
    """Synthetic (paper) trade opened by the paper trading engine."""

    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)
    coin_id: Mapped[int] = mapped_column(ForeignKey("zora_coins.id"), nullable=False)

    size_usd: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    entry_slippage_bps: Mapped[int] = mapped_column(Integer, default=0)
    assumed_fee_bps: Mapped[int] = mapped_column(Integer, default=30)  # 0.3%

    exit_price_usd: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    pnl_usd: Mapped[float | None] = mapped_column(Float)
    pnl_pct: Mapped[float | None] = mapped_column(Float)

    status: Mapped[PositionStatus] = mapped_column(
        Enum(PositionStatus), default=PositionStatus.OPEN, nullable=False, index=True
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Risk parameters baked in at entry
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=0.15)   # 15%
    take_profit_pct: Mapped[float] = mapped_column(Float, default=0.50) # 50%
    timeout_minutes: Mapped[int] = mapped_column(Integer, default=60)

    signal: Mapped["Signal"] = relationship(back_populates="paper_positions")


class LivePosition(TimestampMixin, Base):
    """
    Real on-chain position.
    Only created when LIVE_TRADING_ENABLED=true and operator has approved.
    """

    __tablename__ = "live_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)
    coin_id: Mapped[int] = mapped_column(ForeignKey("zora_coins.id"), nullable=False)

    # On-chain transaction details
    buy_tx_hash: Mapped[str | None] = mapped_column(String(66))
    sell_tx_hash: Mapped[str | None] = mapped_column(String(66))

    size_usd: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price_usd: Mapped[float | None] = mapped_column(Float)
    exit_price_usd: Mapped[float | None] = mapped_column(Float)
    actual_slippage_bps: Mapped[int | None] = mapped_column(Integer)
    gas_cost_usd: Mapped[float | None] = mapped_column(Float)
    pnl_usd: Mapped[float | None] = mapped_column(Float)

    status: Mapped[PositionStatus] = mapped_column(
        Enum(PositionStatus), default=PositionStatus.OPEN, nullable=False, index=True
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    approved_by: Mapped[int] = mapped_column(BigInteger, nullable=False)  # Telegram user ID

    signal: Mapped["Signal"] = relationship(back_populates="live_positions")


class RiskEvent(Base):
    """Record of a safety rule being triggered — used for audit and analysis."""

    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[RiskEventType] = mapped_column(Enum(RiskEventType), nullable=False)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    coin_id: Mapped[int | None] = mapped_column(ForeignKey("zora_coins.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[str | None] = mapped_column(Text)  # JSON blob for extra context
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommandAuditLog(Base):
    """Immutable log of every Telegram command invoked by any user."""

    __tablename__ = "command_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("bot_users.id"), nullable=False)
    command: Mapped[str] = mapped_column(String(64), nullable=False)
    args: Mapped[str | None] = mapped_column(Text)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    result: Mapped[str | None] = mapped_column(String(32))  # "ok" | "denied" | "error"
    invoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["BotUser"] = relationship(back_populates="audit_logs")


class CreatorOverride(TimestampMixin, Base):
    """
    Per-creator or per-coin runtime overrides set by the operator.
    Takes precedence over all scoring and policy decisions.

    Scope:
      - x_username only  → applies to that X account
      - contract_address only → applies to that coin
      - both              → applies to that creator+coin pair
    """

    __tablename__ = "creator_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Scope selectors (at least one must be set)
    x_username: Mapped[str | None] = mapped_column(String(255), index=True)
    contract_address: Mapped[str | None] = mapped_column(String(42), index=True)

    # Override values (None = no override for that field)
    is_blacklisted: Mapped[bool | None] = mapped_column(Boolean)
    is_whitelisted: Mapped[bool | None] = mapped_column(Boolean)
    # Score multiplier applied to final score (e.g. 1.2 = boost 20%, 0.5 = halve)
    score_multiplier: Mapped[float | None] = mapped_column(Float)
    # Free-text reason for the operator
    reason: Mapped[str | None] = mapped_column(Text)
    added_by: Mapped[int | None] = mapped_column(BigInteger)  # Telegram user ID


# ══════════════════════════════════════════════════════════════════════════════
# CREATOR INTENT TRACKING  +  WALLET LINKING  (Phase: Creator Intent)
# ══════════════════════════════════════════════════════════════════════════════

class CreatorWatchMode(str, enum.Enum):
    CREATOR_ONLY = "creator_only"
    KEYWORD_ONLY = "keyword_only"
    HYBRID       = "hybrid"


class PostSentiment(str, enum.Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    NOISE   = "noise"


class WalletLinkStatus(str, enum.Enum):
    PENDING   = "pending"
    VERIFIED  = "verified"
    REVOKED   = "revoked"


class TrackedCreator(TimestampMixin, Base):
    """
    An X/Twitter creator being actively tracked for intent signals.
    Each BotUser can maintain their own watchlist.
    """
    __tablename__ = "tracked_creators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Who is tracking this creator
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # X identity
    x_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    x_username: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    follower_count: Mapped[int | None] = mapped_column(Integer)
    # Linked Zora wallet (resolved when available)
    zora_wallet_address: Mapped[str | None] = mapped_column(String(42))
    # Tracking state
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mode: Mapped[CreatorWatchMode] = mapped_column(
        Enum(CreatorWatchMode), default=CreatorWatchMode.HYBRID, nullable=False
    )
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_post_id: Mapped[str | None] = mapped_column(String(64))  # for since_id polling


class CreatorPost(TimestampMixin, Base):
    """A post ingested from a tracked creator."""
    __tablename__ = "creator_posts"
    __table_args__ = (UniqueConstraint("x_post_id", name="uq_creator_posts_x_post_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracked_creator_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_creators.id"), nullable=False, index=True
    )
    x_post_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    text: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lang: Mapped[str | None] = mapped_column(String(8))
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    retweet_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    quote_count: Mapped[int] = mapped_column(Integer, default=0)
    is_classified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    classification: Mapped["CreatorPostClassification | None"] = relationship(
        back_populates="post", uselist=False
    )
    signal_candidates: Mapped[list["CreatorSignalCandidate"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )


class CreatorPostClassification(TimestampMixin, Base):
    """Classification result for a creator post."""
    __tablename__ = "creator_post_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("creator_posts.id"), nullable=False, unique=True
    )
    # Core classification
    actionable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sentiment: Mapped[PostSentiment] = mapped_column(
        Enum(PostSentiment), default=PostSentiment.NOISE, nullable=False
    )
    confidence: Mapped[int] = mapped_column(Integer, default=0)  # 0–100
    conviction_score: Mapped[int] = mapped_column(Integer, default=0)  # 0–100
    # Extracted signals (stored as JSON text for portability)
    entities_json: Mapped[str | None] = mapped_column(Text)   # JSON list of strings
    keywords_json: Mapped[str | None] = mapped_column(Text)   # JSON list of strings
    narratives_json: Mapped[str | None] = mapped_column(Text) # JSON list of strings
    # Human-readable summary
    summary: Mapped[str | None] = mapped_column(Text)
    # Classification source
    used_llm: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    post: Mapped["CreatorPost"] = relationship(back_populates="classification")


class CreatorSignalCandidate(TimestampMixin, Base):
    """
    A Zora coin candidate discovered for a creator post.
    One post can produce multiple ranked candidates.
    """
    __tablename__ = "creator_signal_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("creator_posts.id"), nullable=False, index=True
    )
    coin_id: Mapped[int | None] = mapped_column(ForeignKey("zora_coins.id"), nullable=True)
    # Coin identity (denormalised for speed — may exist before ZoraCoin row)
    contract_address: Mapped[str | None] = mapped_column(String(42))
    symbol: Mapped[str | None] = mapped_column(String(32))
    # Match type priority
    match_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # "creator_coin" | "content_coin" | "keyword_match" | "trending_match"
    # Score components (all 0–100)
    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    creator_linkage_score: Mapped[int] = mapped_column(Integer, default=0)
    momentum_score: Mapped[int] = mapped_column(Integer, default=0)
    liquidity_score: Mapped[int] = mapped_column(Integer, default=0)
    final_rank_score: Mapped[int] = mapped_column(Integer, default=0)
    # Market snapshot at discovery time
    price_usd: Mapped[float | None] = mapped_column(Float)
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    slippage_bps: Mapped[int | None] = mapped_column(Integer)
    volume_5m_usd: Mapped[float | None] = mapped_column(Float)
    # Risk flags (pipe-separated string, e.g. "low_liquidity|very_new")
    risk_flags: Mapped[str | None] = mapped_column(String(255))
    # Recommended action
    recommendation: Mapped[str | None] = mapped_column(String(32))

    post: Mapped["CreatorPost"] = relationship(back_populates="signal_candidates")


class UserStrategyPreferences(TimestampMixin, Base):
    """Per-Telegram-user strategy preferences."""
    __tablename__ = "user_strategy_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    mode: Mapped[CreatorWatchMode] = mapped_column(
        Enum(CreatorWatchMode), default=CreatorWatchMode.HYBRID, nullable=False
    )
    min_conviction_score: Mapped[int] = mapped_column(Integer, default=50)
    paper_auto_open: Mapped[bool] = mapped_column(Boolean, default=False)


class WalletLink(TimestampMixin, Base):
    """A verified wallet address linked to a Telegram user."""
    __tablename__ = "wallet_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False)
    chain_id: Mapped[int] = mapped_column(Integer, default=8453)  # Base mainnet
    status: Mapped[WalletLinkStatus] = mapped_column(
        Enum(WalletLinkStatus), default=WalletLinkStatus.VERIFIED, nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Linked Zora profile (if found)
    zora_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("zora_profile_links.id"), nullable=True
    )

    zora_profile: Mapped["ZoraProfileLink | None"] = relationship(back_populates="wallet_links")
    nonces: Mapped[list["WalletLinkNonce"]] = relationship(
        back_populates="wallet_link", cascade="all, delete-orphan"
    )


class WalletLinkNonce(Base):
    """Short-lived nonce issued for wallet ownership verification."""
    __tablename__ = "wallet_link_nonces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    wallet_address: Mapped[str | None] = mapped_column(String(42))  # set after wallet connects
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    wallet_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("wallet_links.id"), nullable=True
    )

    wallet_link: Mapped["WalletLink | None"] = relationship(back_populates="nonces")


class ZoraProfileLink(TimestampMixin, Base):
    """Zora profile data linked from a verified wallet."""
    __tablename__ = "zora_profile_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(String(42), unique=True, nullable=False)
    zora_display_name: Mapped[str | None] = mapped_column(String(255))
    zora_bio: Mapped[str | None] = mapped_column(Text)
    zora_profile_url: Mapped[str | None] = mapped_column(Text)
    zora_x_username: Mapped[str | None] = mapped_column(String(255))
    creator_coin_address: Mapped[str | None] = mapped_column(String(42))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    wallet_links: Mapped[list["WalletLink"]] = relationship(back_populates="zora_profile")


# ══════════════════════════════════════════════════════════════════════════════
# PREMIUM TIER + AI CHAT  (Phase: Premium + AI)
# ══════════════════════════════════════════════════════════════════════════════

class SubscriptionTier(str, enum.Enum):
    FREE    = "free"
    PREMIUM = "premium"


class PaymentStatus(str, enum.Enum):
    PENDING   = "pending"
    CONFIRMED = "confirmed"
    EXPIRED   = "expired"
    REFUNDED  = "refunded"


class UserSubscription(TimestampMixin, Base):
    """
    Tracks subscription tier per Telegram user.
    Free tier is default. Premium is unlocked by on-chain payment.
    """
    __tablename__ = "user_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    tier: Mapped[SubscriptionTier] = mapped_column(
        Enum(SubscriptionTier), default=SubscriptionTier.FREE, nullable=False
    )
    # When premium expires (None = lifetime / manual grant)
    premium_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # AI chat toggle — user can turn off even if premium
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Last payment reference
    last_payment_tx: Mapped[str | None] = mapped_column(String(66))


class PremiumPayment(TimestampMixin, Base):
    """
    On-chain payment record for premium subscription.
    Bot monitors Base chain for deposits to the payment address.
    """
    __tablename__ = "premium_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # Payment details
    payment_address: Mapped[str] = mapped_column(String(42), nullable=False)
    expected_amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    token: Mapped[str] = mapped_column(String(10), nullable=False)  # "ETH" or "USDC"
    chain_id: Mapped[int] = mapped_column(Integer, default=8453)
    # Settlement
    tx_hash: Mapped[str | None] = mapped_column(String(66), index=True)
    amount_received: Mapped[float | None] = mapped_column(Float)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # What they get
    subscription_days: Mapped[int] = mapped_column(Integer, default=30)


class ChatMessage(TimestampMixin, Base):
    """
    AI chat history per user.
    Kept for context window (last N messages) and user memory.
    """
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional context — what signal/coin was being discussed
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    coin_address: Mapped[str | None] = mapped_column(String(42))
    # Token usage tracking
    tokens_used: Mapped[int | None] = mapped_column(Integer)


class UserPreferences(TimestampMixin, Base):
    """
    Persistent user preferences remembered by the AI.
    Key-value store per user.
    """
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    preference_key: Mapped[str] = mapped_column(String(64), nullable=False)
    preference_value: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("telegram_user_id", "preference_key",
                         name="uq_user_preferences_user_key"),
    )


class ConversationSession(TimestampMixin, Base):
    """
    OpenAI Responses API conversation session per Telegram user.
    Stores the OpenAI thread_id and assistant_id for per-user conversation context.
    """
    __tablename__ = "conversation_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    openai_thread_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    openai_assistant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Track last activity for session timeout
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Optionally store last N messages for context (JSON blob)
    recent_context_json: Mapped[str | None] = mapped_column(Text)
