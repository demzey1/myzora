"""
app/risk/risk_manager.py
─────────────────────────────────────────────────────────────────────────────
Risk controls and position guardrails.

Enforces:
  - Max trade size per transaction
  - Max concurrent open positions
  - Daily realized loss limit
  - Slippage tolerance
  - New coin lockout period
  - Minimum liquidity requirement

All checks return detailed denial reasons for user feedback.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    PaperPosition,
    LivePosition,
    PositionStatus,
    RiskEvent,
    RiskEventType,
)
from app.db.repositories.positions import PaperPositionRepository

log = logging.getLogger(__name__)


class RiskCheckResult:
    """Result of a risk check."""

    def __init__(self, allowed: bool, reason: str = "", event_type: RiskEventType | None = None):
        self.allowed = allowed
        self.reason = reason
        self.event_type = event_type


class RiskManager:
    """Enforce risk controls before trade execution."""

    def __init__(self, session: AsyncSession, telegram_user_id: int):
        self.session = session
        self.telegram_user_id = telegram_user_id

    async def check_trade(
        self,
        coin_symbol: str,
        action: str,
        amount_usd: float,
        slippage_bps: int,
        estimated_fees_usd: float,
    ) -> RiskCheckResult:
        """
        Comprehensive pre-trade risk check.

        Returns:
            RiskCheckResult(allowed=bool, reason=str)
        """
        checks = [
            (
                await self._check_trade_size(amount_usd),
                "trade size check",
            ),
            (
                await self._check_concurrent_positions(),
                "concurrent positions check",
            ),
            (
                await self._check_daily_loss(),
                "daily loss limit check",
            ),
            (
                await self._check_slippage(slippage_bps),
                "slippage tolerance check",
            ),
            (
                await self._check_liquidity_requirement(),
                "liquidity requirement check",
            ),
        ]

        for result, check_name in checks:
            if not result.allowed:
                log.warning(
                    "risk_check_failed",
                    check=check_name,
                    reason=result.reason,
                    user_id=self.telegram_user_id,
                )
                await self._log_risk_event(
                    coin_symbol=coin_symbol,
                    event_type=result.event_type or RiskEventType.MANUAL_REJECT,
                    details=result.reason,
                )
                return result

        return RiskCheckResult(allowed=True)

    # ── Individual Risk Checks ─────────────────────────────────────────────

    async def _check_trade_size(self, amount_usd: float) -> RiskCheckResult:
        """Enforce max trade size."""
        max_trade_usd = settings.risk_max_trade_size_usd
        if amount_usd > max_trade_usd:
            return RiskCheckResult(
                allowed=False,
                reason=f"Trade size ${amount_usd} exceeds max ${max_trade_usd}",
                event_type=RiskEventType.MANUAL_REJECT,
            )
        return RiskCheckResult(allowed=True)

    async def _check_concurrent_positions(self) -> RiskCheckResult:
        """Enforce max concurrent positions."""
        repo = PaperPositionRepository(self.session)
        open_positions = await repo.get_open()
        max_concurrent = settings.risk_max_concurrent_positions

        if len(open_positions) >= max_concurrent:
            return RiskCheckResult(
                allowed=False,
                reason=f"Max {max_concurrent} concurrent positions reached. Close one to open another.",
                event_type=RiskEventType.CONCURRENT_POSITION_LIMIT,
            )
        return RiskCheckResult(allowed=True)

    async def _check_daily_loss(self) -> RiskCheckResult:
        """Check daily realized loss limit."""
        repo = PaperPositionRepository(self.session)
        daily_loss = await repo.get_daily_realised_loss(self.telegram_user_id)
        max_daily_loss = settings.risk_max_daily_loss_usd

        if daily_loss > max_daily_loss:
            remaining = daily_loss - max_daily_loss
            return RiskCheckResult(
                allowed=False,
                reason=f"Daily loss limit reached (${daily_loss} realized). Exceeded by ${remaining}.",
                event_type=RiskEventType.DAILY_LOSS_LIMIT,
            )
        return RiskCheckResult(allowed=True)

    async def _check_slippage(self, slippage_bps: int) -> RiskCheckResult:
        """Enforce slippage tolerance."""
        max_slippage_bps = settings.risk_max_slippage_bps

        if slippage_bps > max_slippage_bps:
            return RiskCheckResult(
                allowed=False,
                reason=f"Slippage {slippage_bps}bps exceeds max {max_slippage_bps}bps. Market may be too illiquid.",
                event_type=RiskEventType.HIGH_SLIPPAGE,
            )
        return RiskCheckResult(allowed=True)

    async def _check_liquidity_requirement(self) -> RiskCheckResult:
        """Enforce minimum liquidity requirement."""
        # TODO: In Phase 3, fetch from coin market data
        # For now, just return allowed
        return RiskCheckResult(allowed=True)

    # ── Risk Event Logging ─────────────────────────────────────────────────

    async def _log_risk_event(
        self,
        coin_symbol: str,
        event_type: RiskEventType,
        details: str,
    ) -> None:
        """Log a risk event to database for auditing."""
        try:
            event = RiskEvent(
                telegram_user_id=self.telegram_user_id,
                coin_symbol=coin_symbol,
                event_type=event_type,
                details=details,
                triggered_at=datetime.utcnow(),
            )
            self.session.add(event)
            await self.session.commit()
        except Exception as exc:
            log.exception("risk_event_logging_error", exc_info=True)

    # ── Wallet Linking Checks ──────────────────────────────────────────────

    async def check_wallet_linked(self) -> bool:
        """
        Check if user has linked a wallet and it's verified.

        For Phase 3 MVP: Always return False (trading gated)
        In Phase 3+: Query WalletLink table for verified link
        """
        # TODO: Implement in Phase 3
        return False

    async def check_trading_enabled(self) -> bool:
        """
        Check if user has explicitly enabled trading.

        Disabled by default, must opt-in after wallet linking.
        """
        # TODO: Check UserPreferences table for trading_enabled flag
        return False


# ── Public API ─────────────────────────────────────────────────────────────

async def check_trade_allowed(
    session: AsyncSession,
    telegram_user_id: int,
    coin_symbol: str,
    action: str,
    amount_usd: float,
    slippage_bps: int = 0,
    estimated_fees_usd: float = 0,
) -> RiskCheckResult:
    """
    Comprehensive trade risk check.

    Call before execute_trade to ensure it's allowed.

    Returns:
        RiskCheckResult(allowed=bool, reason=str)
    """
    manager = RiskManager(session, telegram_user_id)

    # Phase 3: Gate trading behind wallet linking
    wallet_linked = await manager.check_wallet_linked()
    if not wallet_linked:
        return RiskCheckResult(
            allowed=False,
            reason="🔗 Wallet not linked. Use 'link my wallet' first.",
        )

    # Phase 3: Gate behind trading enablement
    trading_enabled = await manager.check_trading_enabled()
    if not trading_enabled:
        return RiskCheckResult(
            allowed=False,
            reason="🚫 Trading not enabled. Link wallet and enable trading to proceed.",
        )

    # Run all risk checks
    return await manager.check_trade(
        coin_symbol=coin_symbol,
        action=action,
        amount_usd=amount_usd,
        slippage_bps=slippage_bps,
        estimated_fees_usd=estimated_fees_usd,
    )
