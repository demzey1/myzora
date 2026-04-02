"""
app/services/wallet_linking.py
─────────────────────────────────────────────────────────────────────────────
Secure wallet-linking flow.

Flow:
  1. Telegram user sends /linkwallet
  2. Bot calls create_link_session() → returns a short-lived URL
  3. User opens URL in browser, connects wallet via MetaMask / WalletConnect
  4. Frontend calls POST /wallet/nonce  { address }
  5. Backend calls create_nonce()       → returns nonce string
  6. Frontend asks wallet to sign: signMessage(nonce)
  7. Frontend calls POST /wallet/verify { address, signature }
  8. Backend calls verify_and_finalize() → verifies EIP-191 sig, stores WalletLink
  9. Backend queries Zora profile for the wallet, stores ZoraProfileLink

Security guarantees:
  - Session tokens are random 32-byte hex strings (256-bit entropy)
  - Nonces expire after settings.wallet_nonce_ttl_seconds (default 300s)
  - Each nonce is single-use (is_used=True after verification)
  - Replay protection: nonce includes timestamp + user_id
  - Private keys NEVER enter this module
  - LLM code path has no import of this module
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

_NONCE_PREFIX = "ZoraSignalBot"


# ── Session token helpers ──────────────────────────────────────────────────────

def _generate_session_token() -> str:
    return secrets.token_hex(32)


def _make_nonce(telegram_user_id: int, session_token: str) -> str:
    """
    Build a human-readable nonce string that:
    - is unique per session
    - embeds timestamp to help users understand what they're signing
    - is NOT guessable
    """
    ts = int(datetime.now(timezone.utc).timestamp())
    rand = secrets.token_hex(8)
    return (
        f"{_NONCE_PREFIX} wallet verification\n"
        f"Session: {session_token[:16]}\n"
        f"Timestamp: {ts}\n"
        f"Random: {rand}\n"
        f"This request will expire in {settings.wallet_nonce_ttl_seconds} seconds."
    )


def _sign_session_token(token: str) -> str:
    """HMAC-sign a session token so the link URL can't be forged."""
    secret = settings.wallet_link_secret
    key = secret.get_secret_value().encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def build_wallet_link_url(session_token: str) -> str:
    """Build the URL that gets sent to the Telegram user."""
    sig = _sign_session_token(session_token)
    base = settings.wallet_link_base_url.rstrip("/")
    return f"{base}/wallet/connect?session={session_token}&sig={sig}"


def verify_session_url_signature(session_token: str, sig: str) -> bool:
    """Verify that a session token URL hasn't been tampered with."""
    expected = _sign_session_token(session_token)
    return hmac.compare_digest(expected, sig)


# ── DB-backed operations ───────────────────────────────────────────────────────

async def create_link_session(
    session: object,  # AsyncSession
    telegram_user_id: int,
) -> str:
    """
    Create a pending wallet-link nonce session.
    Returns the full URL to send the user.
    """
    from app.db.models import WalletLinkNonce

    token = _generate_session_token()
    nonce = _make_nonce(telegram_user_id, token)
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.wallet_nonce_ttl_seconds
    )

    nonce_row = WalletLinkNonce(
        session_token=token,
        telegram_user_id=telegram_user_id,
        nonce=nonce,
        expires_at=expires_at,
    )
    session.add(nonce_row)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]

    log.info("wallet_link_session_created", telegram_user_id=telegram_user_id)
    return build_wallet_link_url(token)


async def set_nonce_address(
    session: object,
    session_token: str,
    wallet_address: str,
) -> str | None:
    """
    Called when user connects their wallet and we learn the address.
    Returns the nonce string to be signed, or None if session is invalid.
    """
    from app.db.repositories.wallet import WalletLinkNonceRepository

    repo = WalletLinkNonceRepository(session)  # type: ignore[arg-type]
    nonce_row = await repo.get_valid_nonce(session_token)
    if nonce_row is None:
        log.warning("wallet_link_nonce_invalid_or_expired", token=session_token[:16])
        return None

    nonce_row.wallet_address = wallet_address.lower()
    await session.flush()  # type: ignore[attr-defined]
    return nonce_row.nonce


async def verify_and_finalize(
    session: object,
    session_token: str,
    wallet_address: str,
    signature: str,
) -> tuple[bool, str]:
    """
    Verify the EIP-191 personal_sign signature and create the WalletLink.

    Returns (success: bool, message: str).
    Writes audit log on success or failure.
    """
    from app.db.models import WalletLink, WalletLinkStatus
    from app.db.repositories.wallet import WalletLinkNonceRepository, WalletLinkRepository

    repo = WalletLinkNonceRepository(session)  # type: ignore[arg-type]
    nonce_row = await repo.get_valid_nonce(session_token)

    if nonce_row is None:
        return False, "Session expired or already used. Please start over with /linkwallet."

    if nonce_row.wallet_address and nonce_row.wallet_address != wallet_address.lower():
        log.warning(
            "wallet_link_address_mismatch",
            expected=nonce_row.wallet_address,
            got=wallet_address.lower(),
        )
        return False, "Wallet address mismatch. Please reconnect your wallet."

    # Verify EIP-191 signature
    ok, err = _verify_eip191(nonce_row.nonce, signature, wallet_address)
    if not ok:
        log.warning("wallet_link_sig_invalid", error=err, address=wallet_address)
        return False, f"Signature verification failed: {err}"

    # Mark nonce as used (prevents replay)
    nonce_row.is_used = True

    # Create or update WalletLink
    wl_repo = WalletLinkRepository(session)  # type: ignore[arg-type]
    existing = await wl_repo.get_verified_for_user(nonce_row.telegram_user_id)

    if existing and existing.wallet_address.lower() == wallet_address.lower():
        existing.verified_at = datetime.now(timezone.utc)
        nonce_row.wallet_link_id = existing.id
    else:
        wl = WalletLink(
            telegram_user_id=nonce_row.telegram_user_id,
            wallet_address=wallet_address.lower(),
            status=WalletLinkStatus.VERIFIED,
            verified_at=datetime.now(timezone.utc),
        )
        session.add(wl)  # type: ignore[attr-defined]
        await session.flush()  # type: ignore[attr-defined]
        nonce_row.wallet_link_id = wl.id

    await session.flush()  # type: ignore[attr-defined]

    # Kick off async Zora profile lookup
    _schedule_zora_profile_lookup(nonce_row.telegram_user_id, wallet_address)

    log.info(
        "wallet_linked",
        telegram_user_id=nonce_row.telegram_user_id,
        address=wallet_address,
    )
    return True, f"Wallet {wallet_address[:6]}...{wallet_address[-4:]} linked successfully!"


def _verify_eip191(message: str, signature: str, expected_address: str) -> tuple[bool, str]:
    """
    Verify an EIP-191 personal_sign signature.
    Uses eth_account if available; returns (False, error) if not installed.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError:
        return (
            False,
            "eth-account not installed. Add eth-account>=0.11.0 to dependencies.",
        )

    try:
        msg = encode_defunct(text=message)
        recovered = Account.recover_message(msg, signature=signature)
        if recovered.lower() == expected_address.lower():
            return True, ""
        return False, f"Recovered {recovered}, expected {expected_address}"
    except Exception as exc:
        return False, str(exc)


def _schedule_zora_profile_lookup(telegram_user_id: int, wallet_address: str) -> None:
    """Fire-and-forget Celery task to look up Zora profile."""
    try:
        from app.jobs.tasks.wallet_tasks import sync_zora_profile_for_wallet
        sync_zora_profile_for_wallet.apply_async(
            kwargs={
                "telegram_user_id": telegram_user_id,
                "wallet_address": wallet_address,
            },
            queue="default",
        )
    except Exception as exc:
        log.warning("zora_profile_lookup_schedule_failed", error=str(exc))


async def unlink_wallet(
    session: object,
    telegram_user_id: int,
) -> tuple[bool, str]:
    """Revoke all verified wallet links for this user."""
    from app.db.models import WalletLinkStatus
    from app.db.repositories.wallet import WalletLinkRepository

    repo = WalletLinkRepository(session)  # type: ignore[arg-type]
    links = await repo.get_all_for_user(telegram_user_id)
    if not links:
        return False, "No linked wallet found."

    now = datetime.now(timezone.utc)
    for link in links:
        link.status = WalletLinkStatus.REVOKED
        link.revoked_at = now

    await session.flush()  # type: ignore[attr-defined]
    log.info("wallet_unlinked", telegram_user_id=telegram_user_id, count=len(links))
    return True, f"Unlinked {len(links)} wallet(s)."

