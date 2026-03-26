"""Premium subscription + AI chat tables

Revision ID: 0003
Revises: 0002
Create Date: 2024-02-01 00:00:00.000000
"""

from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── user_subscriptions ────────────────────────────────────────────────────
    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "tier",
            sa.Enum("free", "premium", name="subscriptiontier"),
            nullable=False,
            server_default="free",
        ),
        sa.Column("premium_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_payment_tx", sa.String(66), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.create_index("ix_user_subscriptions_telegram_user_id", "user_subscriptions", ["telegram_user_id"])

    # ── premium_payments ──────────────────────────────────────────────────────
    op.create_table(
        "premium_payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("payment_address", sa.String(42), nullable=False),
        sa.Column("expected_amount_usd", sa.Float(), nullable=False),
        sa.Column("token", sa.String(10), nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=False, server_default="8453"),
        sa.Column("tx_hash", sa.String(66), nullable=True),
        sa.Column("amount_received", sa.Float(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "confirmed", "expired", "refunded", name="paymentstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subscription_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_premium_payments_telegram_user_id", "premium_payments", ["telegram_user_id"])
    op.create_index("ix_premium_payments_tx_hash", "premium_payments", ["tx_hash"])

    # ── chat_messages ─────────────────────────────────────────────────────────
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id"), nullable=True),
        sa.Column("coin_address", sa.String(42), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_telegram_user_id", "chat_messages", ["telegram_user_id"])

    # ── user_preferences ──────────────────────────────────────────────────────
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("preference_key", sa.String(64), nullable=False),
        sa.Column("preference_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id", "preference_key", name="uq_user_preferences_user_key"),
    )
    op.create_index("ix_user_preferences_telegram_user_id", "user_preferences", ["telegram_user_id"])


def downgrade() -> None:
    for tbl in ["user_preferences", "chat_messages", "premium_payments", "user_subscriptions"]:
        op.drop_table(tbl)
    for enum in ["subscriptiontier", "paymentstatus"]:
        sa.Enum(name=enum).drop(op.get_bind(), checkfirst=True)
