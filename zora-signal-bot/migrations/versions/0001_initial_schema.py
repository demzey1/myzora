"""Initial schema — all tables from Phase 1–4

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── bot_users ─────────────────────────────────────────────────────────────
    op.create_table(
        "bot_users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("first_name", sa.String(255), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("paper_trading_enabled", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )

    # ── creators ──────────────────────────────────────────────────────────────
    op.create_table(
        "creators",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("wallet_address", sa.String(42), nullable=False),
        sa.Column("zora_profile_url", sa.Text(), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("x_username", sa.String(255), nullable=True),
        sa.Column("historical_conversion_rate", sa.Float(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("wallet_address"),
    )
    op.create_index("ix_creators_x_username", "creators", ["x_username"])

    # ── monitored_accounts ────────────────────────────────────────────────────
    op.create_table(
        "monitored_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("x_user_id", sa.String(64), nullable=False),
        sa.Column("x_username", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_blacklisted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creator_id", sa.Integer(), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("x_user_id"),
    )
    op.create_index("ix_monitored_accounts_x_username", "monitored_accounts", ["x_username"])

    # ── zora_coins ────────────────────────────────────────────────────────────
    op.create_table(
        "zora_coins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contract_address", sa.String(42), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("creator_id", sa.Integer(), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("chain_id", sa.Integer(), nullable=False, server_default="8453"),
        sa.Column("decimals", sa.Integer(), nullable=False, server_default="18"),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_traded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_address"),
    )
    op.create_index("ix_zora_coins_symbol", "zora_coins", ["symbol"])

    # ── posts ─────────────────────────────────────────────────────────────────
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("x_post_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("monitored_accounts.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lang", sa.String(8), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repost_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quote_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("view_count", sa.Integer(), nullable=True),
        sa.Column("is_processed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("zora_coin_id", sa.Integer(),
                  sa.ForeignKey("zora_coins.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("x_post_id", name="uq_posts_x_post_id"),
    )
    op.create_index("ix_posts_x_post_id", "posts", ["x_post_id"])

    # ── post_metrics_snapshots ────────────────────────────────────────────────
    op.create_table(
        "post_metrics_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(),
                  sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repost_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quote_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("view_count", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_post_metrics_snapshots_post_id",
                    "post_metrics_snapshots", ["post_id"])

    # ── coin_market_snapshots ─────────────────────────────────────────────────
    op.create_table(
        "coin_market_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("coin_id", sa.Integer(),
                  sa.ForeignKey("zora_coins.id"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("price_usd", sa.Float(), nullable=True),
        sa.Column("liquidity_usd", sa.Float(), nullable=True),
        sa.Column("volume_5m_usd", sa.Float(), nullable=True),
        sa.Column("volume_1h_usd", sa.Float(), nullable=True),
        sa.Column("volume_24h_usd", sa.Float(), nullable=True),
        sa.Column("market_cap_usd", sa.Float(), nullable=True),
        sa.Column("holder_count", sa.Integer(), nullable=True),
        sa.Column("slippage_bps_reference", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coin_market_snapshots_coin_id",
                    "coin_market_snapshots", ["coin_id"])

    # ── signals ───────────────────────────────────────────────────────────────
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=True),
        sa.Column("coin_id", sa.Integer(), sa.ForeignKey("zora_coins.id"), nullable=True),
        sa.Column("deterministic_score", sa.Float(), nullable=False),
        sa.Column("llm_score", sa.Float(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("llm_meme_strength", sa.Integer(), nullable=True),
        sa.Column("llm_narrative_fit", sa.Integer(), nullable=True),
        sa.Column("llm_conversion_likelihood", sa.Integer(), nullable=True),
        sa.Column("llm_spam_risk", sa.Integer(), nullable=True),
        sa.Column("llm_summary", sa.Text(), nullable=True),
        sa.Column("llm_recommendation_bias", sa.String(16), nullable=True),
        sa.Column("recommendation",
                  sa.Enum("IGNORE", "WATCH", "ALERT", "PAPER_TRADE", "LIVE_TRADE_READY",
                          name="recommendation"),
                  nullable=False),
        sa.Column("risk_notes", sa.Text(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("is_approved", sa.Boolean(), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signals_recommendation", "signals", ["recommendation"])

    # ── paper_positions ───────────────────────────────────────────────────────
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(),
                  sa.ForeignKey("signals.id"), nullable=False),
        sa.Column("coin_id", sa.Integer(),
                  sa.ForeignKey("zora_coins.id"), nullable=False),
        sa.Column("size_usd", sa.Float(), nullable=False),
        sa.Column("entry_price_usd", sa.Float(), nullable=False),
        sa.Column("entry_slippage_bps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assumed_fee_bps", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("exit_price_usd", sa.Float(), nullable=True),
        sa.Column("exit_reason", sa.String(32), nullable=True),
        sa.Column("pnl_usd", sa.Float(), nullable=True),
        sa.Column("pnl_pct", sa.Float(), nullable=True),
        sa.Column("status",
                  sa.Enum("OPEN", "CLOSED", "STOPPED", "EXPIRED", name="positionstatus"),
                  nullable=False, server_default="OPEN"),
        sa.Column("opened_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_loss_pct", sa.Float(), nullable=False, server_default="0.15"),
        sa.Column("take_profit_pct", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("timeout_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_positions_status", "paper_positions", ["status"])

    # ── live_positions ────────────────────────────────────────────────────────
    op.create_table(
        "live_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(),
                  sa.ForeignKey("signals.id"), nullable=False),
        sa.Column("coin_id", sa.Integer(),
                  sa.ForeignKey("zora_coins.id"), nullable=False),
        sa.Column("buy_tx_hash", sa.String(66), nullable=True),
        sa.Column("sell_tx_hash", sa.String(66), nullable=True),
        sa.Column("size_usd", sa.Float(), nullable=False),
        sa.Column("entry_price_usd", sa.Float(), nullable=True),
        sa.Column("exit_price_usd", sa.Float(), nullable=True),
        sa.Column("actual_slippage_bps", sa.Integer(), nullable=True),
        sa.Column("gas_cost_usd", sa.Float(), nullable=True),
        sa.Column("pnl_usd", sa.Float(), nullable=True),
        sa.Column("status",
                  sa.Enum("OPEN", "CLOSED", "STOPPED", "EXPIRED", name="positionstatus"),
                  nullable=False, server_default="OPEN"),
        sa.Column("opened_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_live_positions_status", "live_positions", ["status"])

    # ── risk_events ───────────────────────────────────────────────────────────
    op.create_table(
        "risk_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type",
                  sa.Enum("LOW_LIQUIDITY", "HIGH_SLIPPAGE", "DAILY_LOSS_LIMIT",
                          "CONCURRENT_POSITION_LIMIT", "COIN_COOLDOWN",
                          "NEW_COIN_LOCKOUT", "LOW_CONFIDENCE", "BLACKLISTED",
                          "KILL_SWITCH", "MANUAL_REJECT", name="riskeventtype"),
                  nullable=False),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id"), nullable=True),
        sa.Column("coin_id", sa.Integer(), sa.ForeignKey("zora_coins.id"), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── command_audit_log ─────────────────────────────────────────────────────
    op.create_table(
        "command_audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("command", sa.String(64), nullable=False),
        sa.Column("args", sa.Text(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("result", sa.String(32), nullable=True),
        sa.Column("invoked_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── creator_overrides ─────────────────────────────────────────────────────
    op.create_table(
        "creator_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("x_username", sa.String(255), nullable=True),
        sa.Column("contract_address", sa.String(42), nullable=True),
        sa.Column("is_blacklisted", sa.Boolean(), nullable=True),
        sa.Column("is_whitelisted", sa.Boolean(), nullable=True),
        sa.Column("score_multiplier", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_creator_overrides_x_username",
                    "creator_overrides", ["x_username"])
    op.create_index("ix_creator_overrides_contract_address",
                    "creator_overrides", ["contract_address"])


def downgrade() -> None:
    for tbl in [
        "creator_overrides", "command_audit_log", "risk_events",
        "live_positions", "paper_positions", "signals",
        "coin_market_snapshots", "post_metrics_snapshots", "posts",
        "zora_coins", "monitored_accounts", "creators", "bot_users",
    ]:
        op.drop_table(tbl)

    for enum in ["recommendation", "positionstatus", "riskeventtype"]:
        sa.Enum(name=enum).drop(op.get_bind(), checkfirst=True)
