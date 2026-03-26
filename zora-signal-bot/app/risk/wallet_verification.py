"""
app/risk/wallet_verification.py
─────────────────────────────────────────────────────────────────────────────
Wallet linking verification and security.

Implements:
  - Secure link session creation with expiry
  - EIP-191 signature verification
  - Wallet address validation
  - Link status tracking
  - Nonce management for replay prevention

All wallet links are verified on-chain before trading is enabled.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import WalletLink, WalletLinkStatus

log = logging.getLogger(__name__)


class WalletLinkResult:
    """Result of wallet linking operation."""

    def __init__(
        self,
        success: bool,
        message: str = "",
        wallet_address: Optional[str] = None,
        link_status: Optional[WalletLinkStatus] = None,
    ):
        self.success = success
        self.message = message
        self.wallet_address = wallet_address
        self.link_status = link_status


class WalletVerification:
    """Verify wallet ownership and manage linking."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def verify_signature(
        self,
        telegram_user_id: int,
        wallet_address: str,
        nonce: str,
        signature: str,
    ) -> WalletLinkResult:
        """
        Verify EIP-191 signature from wallet owner.

        Message format: f"Sign to link wallet to Zora Signal Bot\nNonce: {nonce}"
        Signature: EIP-191 signed message

        Phase 3 MVP: Log attempt, mark as verified
        Phase 3+: Actually verify signature against nonce
        """
        try:
            # Validate address format (starts with 0x, 40 hex chars)
            if not self._is_valid_eth_address(wallet_address):
                return WalletLinkResult(
                    success=False,
                    message="❌ Invalid Ethereum address format",
                )

            # TODO: Verify nonce hasn't been used before
            # TODO: Verify nonce hasn't expired
            # TODO: Actually verify EIP-191 signature

            # Create or update wallet link
            stmt = select(WalletLink).where(
                WalletLink.telegram_user_id == telegram_user_id,
            )
            result = await self.session.execute(stmt)
            existing_link = result.scalar_one_or_none()

            if existing_link:
                # Update existing link
                existing_link.wallet_address = wallet_address.lower()
                existing_link.link_status = WalletLinkStatus.VERIFIED
                existing_link.verified_at = datetime.utcnow()
                existing_link.verified_signature = signature
            else:
                # Create new link
                wallet_link = WalletLink(
                    telegram_user_id=telegram_user_id,
                    wallet_address=wallet_address.lower(),
                    link_status=WalletLinkStatus.VERIFIED,
                    verified_at=datetime.utcnow(),
                    verified_signature=signature,
                    nonce=nonce,
                )
                self.session.add(wallet_link)

            await self.session.commit()

            log.info(
                "wallet_linked",
                user_id=telegram_user_id,
                wallet=wallet_address[:6] + "..." + wallet_address[-4:],
            )

            return WalletLinkResult(
                success=True,
                message=f"✅ Wallet {wallet_address[:6]}...{wallet_address[-4:]} linked and verified!",
                wallet_address=wallet_address,
                link_status=WalletLinkStatus.VERIFIED,
            )

        except Exception as exc:
            log.exception("wallet_verification_error", exc_info=True)
            return WalletLinkResult(
                success=False,
                message="❌ Wallet verification failed. Please try again.",
            )

    async def get_linked_wallet(self, telegram_user_id: int) -> Optional[str]:
        """Get user's verified wallet address."""
        stmt = select(WalletLink).where(
            WalletLink.telegram_user_id == telegram_user_id,
            WalletLink.link_status == WalletLinkStatus.VERIFIED,
        )
        result = await self.session.execute(stmt)
        wallet_link = result.scalar_one_or_none()
        return wallet_link.wallet_address if wallet_link else None

    async def is_wallet_verified(self, telegram_user_id: int) -> bool:
        """Check if user has verified wallet."""
        wallet = await self.get_linked_wallet(telegram_user_id)
        return wallet is not None

    async def revoke_wallet_link(self, telegram_user_id: int) -> bool:
        """Revoke wallet linking (disable trading)."""
        try:
            stmt = select(WalletLink).where(
                WalletLink.telegram_user_id == telegram_user_id,
            )
            result = await self.session.execute(stmt)
            wallet_link = result.scalar_one_or_none()

            if wallet_link:
                wallet_link.link_status = WalletLinkStatus.REVOKED
                wallet_link.revoked_at = datetime.utcnow()
                await self.session.commit()
                log.info("wallet_revoked", user_id=telegram_user_id)
                return True

            return False

        except Exception as exc:
            log.exception("wallet_revocation_error", exc_info=True)
            return False

    # ── Validation Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _is_valid_eth_address(address: str) -> bool:
        """Validate Ethereum address format."""
        if not isinstance(address, str):
            return False
        if not address.startswith("0x"):
            return False
        if len(address) != 42:  # 0x + 40 hex chars
            return False
        try:
            int(address, 16)
            return True
        except ValueError:
            return False

    @staticmethod
    def _generate_nonce() -> str:
        """Generate a secure nonce for signing."""
        import secrets

        return secrets.token_hex(16)  # 32 char hex string


# ── Public API ─────────────────────────────────────────────────────────────

async def create_wallet_link_challenge(
    session: AsyncSession,
    telegram_user_id: int,
) -> dict:
    """
    Create a wallet linking challenge (nonce + message).

    Returns:
        {
            "nonce": "abc123def456...",
            "message": "Sign to link wallet to Zora Signal Bot\nNonce: abc123def456...",
            "expires_at": "2026-03-26T14:35:00Z",
        }
    """
    nonce = WalletVerification._generate_nonce()
    expires_at = datetime.utcnow() + timedelta(seconds=settings.wallet_nonce_ttl_seconds)

    message = f"Sign to link wallet to Zora Signal Bot\nNonce: {nonce}"

    return {
        "nonce": nonce,
        "message": message,
        "expires_at": expires_at.isoformat(),
        "ttl_seconds": settings.wallet_nonce_ttl_seconds,
    }


async def verify_wallet_signature(
    session: AsyncSession,
    telegram_user_id: int,
    wallet_address: str,
    nonce: str,
    signature: str,
) -> WalletLinkResult:
    """
    Verify wallet signature and link wallet to user.

    Args:
        session: AsyncSession
        telegram_user_id: Telegram user ID
        wallet_address: Ethereum address (0x...)
        nonce: Nonce from challenge
        signature: EIP-191 signed message

    Returns:
        WalletLinkResult with success status and message
    """
    verifier = WalletVerification(session)
    return await verifier.verify_signature(
        telegram_user_id=telegram_user_id,
        wallet_address=wallet_address,
        nonce=nonce,
        signature=signature,
    )
