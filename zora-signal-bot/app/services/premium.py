"""
app/services/premium.py
─────────────────────────────────────────────────────────────────────────────
Premium subscription management via on-chain crypto payments (Base network).

Flow:
  1. User sends /premium or /subscribe
  2. Bot creates a PremiumPayment record with the payment address + amount
  3. Bot shows user the payment address and amount
  4. User sends ETH or USDC to that address on Base
  5. Celery task monitors Base chain for the deposit (every 60s)
  6. When payment confirmed → UserSubscription upgraded to PREMIUM
  7. Bot sends confirmation via Telegram

Pricing (configurable via env):
  PREMIUM_PRICE_USD=9.99   (default 30 days)
  PREMIUM_PAYMENT_ADDRESS= (your receiving wallet address on Base)

We use USDC on Base for stable pricing, ETH also accepted at live rate.
USDC contract on Base: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

# USDC on Base mainnet (verified)
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6

PREMIUM_PRICE_USD      = 9.99   # monthly price
SUBSCRIPTION_DAYS      = 30
PAYMENT_EXPIRY_MINUTES = 60     # user has 60 min to pay


async def create_payment_request(
    session: Any,
    telegram_user_id: int,
) -> dict:
    """
    Create a new premium payment request.
    Returns payment details to show the user.
    """
    from app.db.models import PremiumPayment
    from app.db.repositories.ai import PremiumPaymentRepository

    if not settings.premium_payment_address:
        return {
            "error": "Premium payments not configured. "
                     "Set PREMIUM_PAYMENT_ADDRESS in .env."
        }

    repo = PremiumPaymentRepository(session)

    # Cancel any existing pending payment
    existing = await repo.get_pending_for_user(telegram_user_id)
    if existing:
        from app.db.models import PaymentStatus
        existing.status = PaymentStatus.EXPIRED

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=PAYMENT_EXPIRY_MINUTES)

    payment = PremiumPayment(
        telegram_user_id=telegram_user_id,
        payment_address=settings.premium_payment_address,
        expected_amount_usd=PREMIUM_PRICE_USD,
        token="USDC",
        chain_id=8453,
        expires_at=expires_at,
        subscription_days=SUBSCRIPTION_DAYS,
    )
    session.add(payment)
    await session.flush()

    # Get current ETH price for ETH equivalent
    eth_price = await _get_eth_price()
    eth_amount = PREMIUM_PRICE_USD / eth_price if eth_price else None

    log.info("premium_payment_created",
             telegram_user_id=telegram_user_id, payment_id=payment.id)

    return {
        "payment_id": payment.id,
        "payment_address": settings.premium_payment_address,
        "usdc_amount": PREMIUM_PRICE_USD,
        "eth_amount": round(eth_amount, 5) if eth_amount else None,
        "eth_price_usd": round(eth_price, 2) if eth_price else None,
        "expires_at": expires_at,
        "subscription_days": SUBSCRIPTION_DAYS,
        "network": "Base (chain ID 8453)",
        "usdc_contract": USDC_BASE,
    }


async def verify_payment_onchain(payment_id: int, session: Any) -> bool:
    """
    Check Base chain for an incoming USDC or ETH transfer to the payment address.
    Called by the Celery payment monitor task.
    Returns True if payment confirmed.
    """
    from app.db.models import PaymentStatus
    from app.db.repositories.ai import PremiumPaymentRepository, UserSubscriptionRepository

    repo = PremiumPaymentRepository(session)
    payment = await repo.get(payment_id)

    if not payment or payment.status != PaymentStatus.PENDING:
        return False

    # Check expiry
    expires = payment.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        payment.status = PaymentStatus.EXPIRED
        log.info("premium_payment_expired", payment_id=payment_id)
        return False

    # Query Alchemy for transfers to our payment address
    tx_hash, amount = await _check_usdc_transfer(
        to_address=payment.payment_address,
        expected_amount=payment.expected_amount_usd,
    )

    if not tx_hash:
        # Also check ETH
        tx_hash, amount = await _check_eth_transfer(
            to_address=payment.payment_address,
            expected_usd=payment.expected_amount_usd,
        )

    if not tx_hash:
        return False

    # Confirm payment
    payment.status = PaymentStatus.CONFIRMED
    payment.tx_hash = tx_hash
    payment.amount_received = amount
    payment.confirmed_at = datetime.now(timezone.utc)

    # Upgrade subscription
    sub_repo = UserSubscriptionRepository(session)
    await sub_repo.upgrade_to_premium(
        telegram_user_id=payment.telegram_user_id,
        days=payment.subscription_days,
        tx_hash=tx_hash,
    )

    log.info(
        "premium_payment_confirmed",
        payment_id=payment_id,
        telegram_user_id=payment.telegram_user_id,
        tx_hash=tx_hash,
    )

    # Notify user
    _notify_premium_activated(payment.telegram_user_id, payment.subscription_days)
    return True


async def _check_usdc_transfer(
    to_address: str, expected_amount: float
) -> tuple[str | None, float | None]:
    """
    Use Alchemy's getAssetTransfers to find incoming USDC to the payment address.
    Returns (tx_hash, amount) or (None, None).
    """
    if not settings.alchemy_api_key:
        return None, None

    rpc_url = settings.base_rpc_url_resolved
    # Expected USDC amount with 10% tolerance
    min_amount = expected_amount * 0.9

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(rpc_url, json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "alchemy_getAssetTransfers",
                "params": [{
                    "toAddress": to_address,
                    "contractAddresses": [USDC_BASE],
                    "category": ["erc20"],
                    "order": "desc",
                    "maxCount": "0x5",
                }]
            })
            data = resp.json()
            transfers = data.get("result", {}).get("transfers", [])

            for tx in transfers:
                amount = float(tx.get("value") or 0)
                if amount >= min_amount:
                    return tx.get("hash"), amount
    except Exception as exc:
        log.warning("usdc_transfer_check_failed", error=str(exc))

    return None, None


async def _check_eth_transfer(
    to_address: str, expected_usd: float
) -> tuple[str | None, float | None]:
    """Check for ETH transfers to the payment address."""
    if not settings.alchemy_api_key:
        return None, None

    eth_price = await _get_eth_price()
    if not eth_price:
        return None, None

    min_eth = (expected_usd * 0.9) / eth_price
    rpc_url = settings.base_rpc_url_resolved

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(rpc_url, json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "alchemy_getAssetTransfers",
                "params": [{
                    "toAddress": to_address,
                    "category": ["external"],
                    "order": "desc",
                    "maxCount": "0x5",
                }]
            })
            data = resp.json()
            transfers = data.get("result", {}).get("transfers", [])

            for tx in transfers:
                eth_amount = float(tx.get("value") or 0)
                if eth_amount >= min_eth:
                    return tx.get("hash"), eth_amount * eth_price
    except Exception as exc:
        log.warning("eth_transfer_check_failed", error=str(exc))

    return None, None


async def _get_eth_price() -> float | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "ethereum", "vs_currencies": "usd"},
            )
            return float(resp.json()["ethereum"]["usd"])
    except Exception:
        return None


def _notify_premium_activated(telegram_user_id: int, days: int) -> None:
    """Fire-and-forget Celery task to notify user."""
    try:
        from app.jobs.tasks.premium_tasks import notify_premium_activated
        notify_premium_activated.apply_async(
            kwargs={"telegram_user_id": telegram_user_id, "days": days},
            queue="alerts",
        )
    except Exception as exc:
        log.warning("premium_notify_failed", error=str(exc))
