"""
app/db/repositories/wallet.py
Repositories for WalletLink, WalletLinkNonce, ZoraProfileLink.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import WalletLink, WalletLinkNonce, WalletLinkStatus, ZoraProfileLink
from app.db.repositories.base import BaseRepository


class WalletLinkRepository(BaseRepository[WalletLink]):
    model = WalletLink

    async def get_verified_for_user(self, telegram_user_id: int) -> WalletLink | None:
        result = await self.session.execute(
            select(WalletLink).where(
                WalletLink.telegram_user_id == telegram_user_id,
                WalletLink.status == WalletLinkStatus.VERIFIED,
            ).order_by(WalletLink.verified_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_all_for_user(self, telegram_user_id: int) -> list[WalletLink]:
        result = await self.session.execute(
            select(WalletLink).where(
                WalletLink.telegram_user_id == telegram_user_id
            ).order_by(WalletLink.created_at.desc())
        )
        return list(result.scalars().all())


class WalletLinkNonceRepository(BaseRepository[WalletLinkNonce]):
    model = WalletLinkNonce

    async def get_by_session_token(self, token: str) -> WalletLinkNonce | None:
        result = await self.session.execute(
            select(WalletLinkNonce).where(WalletLinkNonce.session_token == token)
        )
        return result.scalar_one_or_none()

    async def get_valid_nonce(self, token: str) -> WalletLinkNonce | None:
        """Return a non-expired, non-used nonce for the given session token."""
        nonce = await self.get_by_session_token(token)
        if nonce is None:
            return None
        now = datetime.now(timezone.utc)
        expires = nonce.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if nonce.is_used or now > expires:
            return None
        return nonce


class ZoraProfileLinkRepository(BaseRepository[ZoraProfileLink]):
    model = ZoraProfileLink

    async def get_by_wallet(self, wallet_address: str) -> ZoraProfileLink | None:
        result = await self.session.execute(
            select(ZoraProfileLink).where(
                ZoraProfileLink.wallet_address == wallet_address.lower()
            )
        )
        return result.scalar_one_or_none()
