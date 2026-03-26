"""
app/db/repositories/ai.py
Repositories for ChatMessage, UserPreferences, UserSubscription, PremiumPayment.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, func, and_

from app.db.models import (
    ChatMessage,
    UserPreferences,
    UserSubscription,
    PremiumPayment,
    SubscriptionTier,
    PaymentStatus,
)
from app.db.repositories.base import BaseRepository


class ChatMessageRepository(BaseRepository[ChatMessage]):
    model = ChatMessage

    async def get_recent(
        self, telegram_user_id: int, limit: int = 10
    ) -> list[ChatMessage]:
        result = await self.session.execute(
            select(ChatMessage)
            .where(ChatMessage.telegram_user_id == telegram_user_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        msgs = list(result.scalars().all())
        return list(reversed(msgs))  # chronological order

    async def save_message(
        self,
        telegram_user_id: int,
        role: str,
        content: str,
        signal_id: int | None = None,
        coin_address: str | None = None,
        tokens_used: int | None = None,
    ) -> ChatMessage:
        msg = ChatMessage(
            telegram_user_id=telegram_user_id,
            role=role,
            content=content,
            signal_id=signal_id,
            coin_address=coin_address,
            tokens_used=tokens_used,
        )
        return await self.add(msg)

    async def count_today(self, telegram_user_id: int) -> int:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        result = await self.session.execute(
            select(func.count(ChatMessage.id)).where(
                and_(
                    ChatMessage.telegram_user_id == telegram_user_id,
                    ChatMessage.role == "user",
                    ChatMessage.created_at >= today_start,
                )
            )
        )
        return result.scalar() or 0

    async def clear_history(self, telegram_user_id: int) -> int:
        """Delete all chat history for a user. Returns count deleted."""
        result = await self.session.execute(
            select(ChatMessage).where(
                ChatMessage.telegram_user_id == telegram_user_id
            )
        )
        msgs = list(result.scalars().all())
        for msg in msgs:
            await self.session.delete(msg)
        return len(msgs)


class UserPreferencesRepository(BaseRepository[UserPreferences]):
    model = UserPreferences

    async def get_all(self, telegram_user_id: int) -> dict[str, str]:
        result = await self.session.execute(
            select(UserPreferences).where(
                UserPreferences.telegram_user_id == telegram_user_id
            )
        )
        prefs = result.scalars().all()
        return {p.preference_key: p.preference_value for p in prefs}

    async def set_preference(
        self, telegram_user_id: int, key: str, value: str
    ) -> None:
        result = await self.session.execute(
            select(UserPreferences).where(
                and_(
                    UserPreferences.telegram_user_id == telegram_user_id,
                    UserPreferences.preference_key == key,
                )
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.preference_value = value
        else:
            pref = UserPreferences(
                telegram_user_id=telegram_user_id,
                preference_key=key,
                preference_value=value,
            )
            self.session.add(pref)

    async def get(self, telegram_user_id: int, key: str) -> str | None:
        result = await self.session.execute(
            select(UserPreferences).where(
                and_(
                    UserPreferences.telegram_user_id == telegram_user_id,
                    UserPreferences.preference_key == key,
                )
            )
        )
        pref = result.scalar_one_or_none()
        return pref.preference_value if pref else None


class UserSubscriptionRepository(BaseRepository[UserSubscription]):
    model = UserSubscription

    async def get_for_user(self, telegram_user_id: int) -> UserSubscription | None:
        result = await self.session.execute(
            select(UserSubscription).where(
                UserSubscription.telegram_user_id == telegram_user_id
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, telegram_user_id: int) -> UserSubscription:
        existing = await self.get_for_user(telegram_user_id)
        if existing:
            return existing
        sub = UserSubscription(telegram_user_id=telegram_user_id)
        return await self.add(sub)

    async def is_premium(self, telegram_user_id: int) -> bool:
        sub = await self.get_for_user(telegram_user_id)
        if not sub or sub.tier != SubscriptionTier.PREMIUM:
            return False
        if sub.premium_expires_at:
            expires = sub.premium_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires:
                return False
        return True

    async def is_ai_enabled(self, telegram_user_id: int) -> bool:
        sub = await self.get_for_user(telegram_user_id)
        if not sub:
            return True  # default on
        return sub.ai_enabled

    async def set_ai_enabled(self, telegram_user_id: int, enabled: bool) -> None:
        sub = await self.get_or_create(telegram_user_id)
        sub.ai_enabled = enabled

    async def upgrade_to_premium(
        self, telegram_user_id: int, days: int, tx_hash: str
    ) -> UserSubscription:
        from datetime import timedelta
        sub = await self.get_or_create(telegram_user_id)
        now = datetime.now(timezone.utc)
        # Extend from current expiry if still active
        current_expiry = sub.premium_expires_at
        if current_expiry and current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)
        base = current_expiry if (current_expiry and current_expiry > now) else now
        sub.tier = SubscriptionTier.PREMIUM
        sub.premium_expires_at = base + timedelta(days=days)
        sub.last_payment_tx = tx_hash
        return sub


class PremiumPaymentRepository(BaseRepository[PremiumPayment]):
    model = PremiumPayment

    async def get_pending_for_user(
        self, telegram_user_id: int
    ) -> PremiumPayment | None:
        result = await self.session.execute(
            select(PremiumPayment).where(
                and_(
                    PremiumPayment.telegram_user_id == telegram_user_id,
                    PremiumPayment.status == PaymentStatus.PENDING,
                )
            ).order_by(PremiumPayment.created_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_tx_hash(self, tx_hash: str) -> PremiumPayment | None:
        result = await self.session.execute(
            select(PremiumPayment).where(PremiumPayment.tx_hash == tx_hash)
        )
        return result.scalar_one_or_none()

    async def get_all_pending(self) -> list[PremiumPayment]:
        result = await self.session.execute(
            select(PremiumPayment).where(
                PremiumPayment.status == PaymentStatus.PENDING
            )
        )
        return list(result.scalars().all())
