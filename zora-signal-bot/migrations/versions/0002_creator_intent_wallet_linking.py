"""Creator intent tracking + wallet linking tables

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-15 00:00:00.000000
"""

from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enums are created automatically by op.create_table via sa.Enum below

    # ── tracked_creators ──────────────────────────────────────────────────────
    op.create_table(
        "tracked_creators",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("x_user_id", sa.String(64), nullable=False),
        sa.Column("x_username", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("zora_wallet_address", sa.String(42), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "mode",
            sa.Enum("creator_only", "keyword_only", "hybrid", name="creatorwatchmode"),
            nullable=False,
            server_default="hybrid",
        ),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_post_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tracked_creators_telegram_user_id", "tracked_creators", ["telegram_user_id"])
    op.create_index("ix_tracked_creators_x_username", "tracked_creators", ["x_username"])

    # ── creator_posts ─────────────────────────────────────────────────────────
    op.create_table(
        "creator_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "tracked_creator_id",
            sa.Integer(),
            sa.ForeignKey("tracked_creators.id"),
            nullable=False,
        ),
        sa.Column("x_post_id", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lang", sa.String(8), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retweet_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quote_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_classified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("x_post_id", name="uq_creator_posts_x_post_id"),
    )
    op.create_index("ix_creator_posts_tracked_creator_id", "creator_posts", ["tracked_creator_id"])
    op.create_index("ix_creator_posts_x_post_id", "creator_posts", ["x_post_id"])

    # ── creator_post_classifications ──────────────────────────────────────────
    op.create_table(
        "creator_post_classifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("creator_posts.id"),
            nullable=False,
        ),
        sa.Column("actionable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "sentiment",
            sa.Enum("bullish", "bearish", "neutral", "noise", name="postsentiment"),
            nullable=False,
            server_default="noise",
        ),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conviction_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entities_json", sa.Text(), nullable=True),
        sa.Column("keywords_json", sa.Text(), nullable=True),
        sa.Column("narratives_json", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("used_llm", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id"),
    )

    # ── creator_signal_candidates ─────────────────────────────────────────────
    op.create_table(
        "creator_signal_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("creator_posts.id"),
            nullable=False,
        ),
        sa.Column(
            "coin_id",
            sa.Integer(),
            sa.ForeignKey("zora_coins.id"),
            nullable=True,
        ),
        sa.Column("contract_address", sa.String(42), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("match_type", sa.String(32), nullable=False),
        sa.Column("relevance_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("creator_linkage_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("momentum_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("liquidity_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("final_rank_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_usd", sa.Float(), nullable=True),
        sa.Column("liquidity_usd", sa.Float(), nullable=True),
        sa.Column("slippage_bps", sa.Integer(), nullable=True),
        sa.Column("volume_5m_usd", sa.Float(), nullable=True),
        sa.Column("risk_flags", sa.String(255), nullable=True),
        sa.Column("recommendation", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_creator_signal_candidates_post_id", "creator_signal_candidates", ["post_id"])

    # ── user_strategy_preferences ─────────────────────────────────────────────
    op.create_table(
        "user_strategy_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum("creator_only", "keyword_only", "hybrid", name="creatorwatchmode"),
            nullable=False,
            server_default="hybrid",
        ),
        sa.Column("min_conviction_score", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("paper_auto_open", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )

    # ── zora_profile_links ────────────────────────────────────────────────────
    op.create_table(
        "zora_profile_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("wallet_address", sa.String(42), nullable=False),
        sa.Column("zora_display_name", sa.String(255), nullable=True),
        sa.Column("zora_bio", sa.Text(), nullable=True),
        sa.Column("zora_profile_url", sa.Text(), nullable=True),
        sa.Column("zora_x_username", sa.String(255), nullable=True),
        sa.Column("creator_coin_address", sa.String(42), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("wallet_address"),
    )

    # ── wallet_links ──────────────────────────────────────────────────────────
    op.create_table(
        "wallet_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("wallet_address", sa.String(42), nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=False, server_default="8453"),
        sa.Column(
            "status",
            sa.Enum("pending", "verified", "revoked", name="walletlinkstatus"),
            nullable=False,
            server_default="verified",
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "zora_profile_id",
            sa.Integer(),
            sa.ForeignKey("zora_profile_links.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wallet_links_telegram_user_id", "wallet_links", ["telegram_user_id"])

    # ── wallet_link_nonces ────────────────────────────────────────────────────
    op.create_table(
        "wallet_link_nonces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_token", sa.String(128), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("wallet_address", sa.String(42), nullable=True),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("is_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "wallet_link_id",
            sa.Integer(),
            sa.ForeignKey("wallet_links.id"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_token"),
    )
    op.create_index("ix_wallet_link_nonces_session_token", "wallet_link_nonces", ["session_token"])


def downgrade() -> None:
    for tbl in [
        "wallet_link_nonces",
        "wallet_links",
        "zora_profile_links",
        "user_strategy_preferences",
        "creator_signal_candidates",
        "creator_post_classifications",
        "creator_posts",
        "tracked_creators",
    ]:
        op.drop_table(tbl)

    for enum in ["creatorwatchmode", "postsentiment", "walletlinkstatus"]:
        sa.Enum(name=enum).drop(op.get_bind(), checkfirst=True)
