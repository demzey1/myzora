"""
app/trading/paper_engine.py
─────────────────────────────────────────────────────────────────────────────
Paper trading engine.

Responsibilities:
  - Open synthetic positions (buy) when a signal is approved
  - Close positions on stop-loss / take-profit / timeout / manual close
  - Track PnL in USD and percentage terms
  - Apply simulated fees and slippage at entry and exit
  - Enforce all risk rules via RiskManager before opening

PnL formula:
  entry_cost  = size_usd * (1 + entry_slippage_bps/10000) * (1 + fee_bps/10000)
  exit_value  = size_usd * (exit_price / entry_price) * (1 - exit_slippage_bps/10000) * (1 - fee_bps/10000)
  pnl_usd     = exit_value - entry_cost
  pnl_pct     = pnl_usd / entry_cost * 100

Exit reasons:
  STOP_LOSS    — price dropped below entry * (1 - stop_loss_pct)
  TAKE_PROFIT  — price rose above entry * (1 + take_profit_pct)
  TIMEOUT      — position held longer than timeout_minutes
  MANUAL       — operator closed via Telegram
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import PaperPosition, PositionStatus, RiskEventType, Signal, ZoraCoin
from app.db.repositories import (
    CoinMarketSnapshotRepository,
    RiskEventRepository,
    SignalRepository,
    ZoraCoinRepository,
)
from app.db.repositories.positions import PaperPositionRepository
from app.logging_config import get_logger
from app.trading.risk_manager import RiskContext, get_risk_manager

log = get_logger(__name__)

# Assumed round-trip fee per side in basis points (0.3% each way = 30 bps)
DEFAULT_FEE_BPS = 30
# Assumed exit slippage when we don't have a live estimate
DEFAULT_EXIT_SLIPPAGE_BPS = 50


@dataclass
class OpenPositionResult:
    success: bool
    position_id: int | None = None
    blocked_by: str | None = None
    message: str = ""


@dataclass
class ClosePositionResult:
    success: bool
    position_id: int
    pnl_usd: float | None = None
    pnl_pct: float | None = None
    exit_reason: str = ""
    message: str = ""


class PaperTradingEngine:
    """
    All paper trading operations.
    Each method takes an AsyncSession and is called within the caller's
    unit-of-work (the Celery task or command handler manages the session).
    """

    async def open_position(
        self,
        session: AsyncSession,
        signal_id: int,
        approved_by_user_id: int | None = None,
        kill_switch: bool = False,
    ) -> OpenPositionResult:
        """
        Open a paper position for the given signal.
        Runs all risk checks first; returns blocked result if any fail.
        """
        sig_repo = SignalRepository(session)
        coin_repo = ZoraCoinRepository(session)
        market_repo = CoinMarketSnapshotRepository(session)
        risk_repo = RiskEventRepository(session)
        pos_repo = PaperPositionRepository(session)

        signal = await sig_repo.get(signal_id)
        if signal is None:
            return OpenPositionResult(success=False, message=f"Signal {signal_id} not found")

        if signal.coin_id is None:
            return OpenPositionResult(success=False, message="Signal has no associated coin")

        coin = await coin_repo.get(signal.coin_id)
        if coin is None:
            return OpenPositionResult(success=False, message="Coin not found")

        # Fetch latest market snapshot for live price / liquidity
        market = await market_repo.get_latest_for_coin(coin.id)
        price = market.price_usd if market else None
        if price is None:
            return OpenPositionResult(
                success=False, message="No price data available — cannot open position"
            )

        # Build daily loss / open position counts
        daily_loss = await pos_repo.get_daily_realised_loss(session)
        open_count = await pos_repo.count_open()

        risk_ctx = RiskContext(
            signal_id=signal_id,
            final_score=signal.final_score,
            coin_id=coin.id,
            contract_address=coin.contract_address,
            coin_launched_at=coin.launched_at,
            last_traded_at=coin.last_traded_at,
            is_blacklisted=await _is_blacklisted(session, coin),
            liquidity_usd=market.liquidity_usd if market else None,
            slippage_bps=market.slippage_bps_reference if market else None,
            daily_realised_loss_usd=daily_loss,
            open_position_count=open_count,
        )

        decision = get_risk_manager().evaluate(risk_ctx, kill_switch=kill_switch)

        if not decision.allowed:
            log.warning(
                "paper_position_blocked",
                signal_id=signal_id,
                rule=decision.blocking_rule,
            )
            if decision.risk_event_type:
                await risk_repo.log_event(
                    event_type=decision.risk_event_type,
                    signal_id=signal_id,
                    coin_id=coin.id,
                    description=decision.blocking_rule,
                )
            return OpenPositionResult(
                success=False,
                blocked_by=decision.blocking_rule,
                message=f"Risk rule blocked: {decision.blocking_rule}",
            )

        # Apply entry slippage
        entry_slippage = market.slippage_bps_reference if (market and market.slippage_bps_reference) else 0

        position = PaperPosition(
            signal_id=signal_id,
            coin_id=coin.id,
            size_usd=settings.paper_trade_size_usd,
            entry_price_usd=price,
            entry_slippage_bps=entry_slippage,
            assumed_fee_bps=DEFAULT_FEE_BPS,
            status=PositionStatus.OPEN,
        )
        await pos_repo.add(position)

        # Update coin's last_traded_at for cooldown enforcement
        coin.last_traded_at = datetime.now(timezone.utc)
        await coin_repo.save(coin)

        # Mark signal as approved
        signal.is_approved = True
        signal.approved_by = approved_by_user_id
        signal.approved_at = datetime.now(timezone.utc)
        await sig_repo.save(signal)

        log.info(
            "paper_position_opened",
            position_id=position.id,
            signal_id=signal_id,
            coin=coin.symbol,
            size_usd=position.size_usd,
            entry_price=price,
        )
        return OpenPositionResult(success=True, position_id=position.id)

    async def close_position(
        self,
        session: AsyncSession,
        position_id: int,
        exit_price_usd: float,
        exit_reason: str,
    ) -> ClosePositionResult:
        """
        Close an open paper position at the given price.
        Applies exit fee and slippage, computes PnL.
        """
        pos_repo = PaperPositionRepository(session)
        position = await pos_repo.get(position_id)

        if position is None:
            return ClosePositionResult(
                success=False, position_id=position_id, message="Position not found"
            )
        if position.status != PositionStatus.OPEN:
            return ClosePositionResult(
                success=False, position_id=position_id, message="Position already closed"
            )

        pnl_usd, pnl_pct = _compute_pnl(
            size_usd=position.size_usd,
            entry_price=position.entry_price_usd,
            exit_price=exit_price_usd,
            entry_slippage_bps=position.entry_slippage_bps,
            exit_slippage_bps=DEFAULT_EXIT_SLIPPAGE_BPS,
            fee_bps=position.assumed_fee_bps,
        )

        exit_status = {
            "STOP_LOSS": PositionStatus.STOPPED,
            "TIMEOUT": PositionStatus.EXPIRED,
        }.get(exit_reason, PositionStatus.CLOSED)

        position.exit_price_usd = exit_price_usd
        position.exit_reason = exit_reason
        position.pnl_usd = round(pnl_usd, 4)
        position.pnl_pct = round(pnl_pct, 2)
        position.status = exit_status
        position.closed_at = datetime.now(timezone.utc)
        await pos_repo.save(position)

        log.info(
            "paper_position_closed",
            position_id=position_id,
            exit_reason=exit_reason,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
        )
        return ClosePositionResult(
            success=True,
            position_id=position_id,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
        )

    async def check_exit_conditions(
        self,
        session: AsyncSession,
        position: PaperPosition,
        current_price_usd: float,
    ) -> str | None:
        """
        Check whether exit conditions are met for an open position.
        Returns the exit_reason string or None if position should stay open.
        """
        now = datetime.now(timezone.utc)
        entry = position.entry_price_usd

        # Stop-loss
        stop_price = entry * (1.0 - position.stop_loss_pct)
        if current_price_usd <= stop_price:
            return "STOP_LOSS"

        # Take-profit
        tp_price = entry * (1.0 + position.take_profit_pct)
        if current_price_usd >= tp_price:
            return "TAKE_PROFIT"

        # Timeout
        opened = position.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        elapsed_minutes = (now - opened).total_seconds() / 60
        if elapsed_minutes >= position.timeout_minutes:
            return "TIMEOUT"

        return None


# ── PnL calculator (pure function — testable in isolation) ────────────────────

def _compute_pnl(
    size_usd: float,
    entry_price: float,
    exit_price: float,
    entry_slippage_bps: int,
    exit_slippage_bps: int,
    fee_bps: int,
) -> tuple[float, float]:
    """
    Returns (pnl_usd, pnl_pct).

    entry_cost  = size * (1 + entry_slippage/10000) * (1 + fee/10000)
    tokens      = size / entry_price          [conceptual units]
    exit_value  = tokens * exit_price * (1 - exit_slippage/10000) * (1 - fee/10000)
    pnl_usd     = exit_value - entry_cost
    pnl_pct     = pnl_usd / entry_cost * 100
    """
    entry_cost = size_usd * (1 + entry_slippage_bps / 10_000) * (1 + fee_bps / 10_000)
    if entry_price <= 0:
        return 0.0, 0.0
    tokens = size_usd / entry_price
    exit_value = tokens * exit_price * (1 - exit_slippage_bps / 10_000) * (1 - fee_bps / 10_000)
    pnl_usd = exit_value - entry_cost
    pnl_pct = (pnl_usd / entry_cost) * 100 if entry_cost > 0 else 0.0
    return pnl_usd, pnl_pct


# ── Singleton ──────────────────────────────────────────────────────────────────
_paper_engine: PaperTradingEngine | None = None


def get_paper_engine() -> PaperTradingEngine:
    global _paper_engine
    if _paper_engine is None:
        _paper_engine = PaperTradingEngine()
    return _paper_engine


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _is_blacklisted(session, coin: "ZoraCoin") -> bool:
    """Check the creator_overrides table for blacklist status."""
    from app.db.repositories.overrides import CreatorOverrideRepository
    from app.db.repositories.accounts import MonitoredAccountRepository
    repo = CreatorOverrideRepository(session)

    # Check by contract address
    if await repo.is_blacklisted(contract_address=coin.contract_address):
        return True

    # Check by creator X username if available
    if coin.creator_id:
        from app.db.models import Creator
        creator = await session.get(Creator, coin.creator_id)
        if creator and creator.x_username:
            if await repo.is_blacklisted(x_username=creator.x_username):
                return True
    return False
