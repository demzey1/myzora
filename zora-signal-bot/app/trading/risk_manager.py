"""
app/trading/risk_manager.py
─────────────────────────────────────────────────────────────────────────────
Deterministic risk rule enforcement.

Every rule that can block a trade lives here.
The paper engine AND the live execution adapter both call this before opening
a position — same guard regardless of trade mode.

Rules (in evaluation order):
  1. Kill switch active                    → REJECT
  2. Signal confidence below threshold     → REJECT
  3. Coin on cooldown                      → REJECT
  4. Coin launched within lockout window   → REJECT
  5. Liquidity below minimum               → REJECT
  6. Slippage exceeds maximum              → REJECT
  7. Daily loss limit reached              → REJECT
  8. Concurrent position cap reached       → REJECT
  9. Creator/coin blacklisted              → REJECT
 10. All rules pass                        → ALLOW

Returns a RiskDecision dataclass with:
  - allowed: bool
  - blocking_rule: str | None
  - risk_event_type: RiskEventType | None   (for DB logging)
  - notes: list[str]                        (advisory, non-blocking)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db.models import RiskEventType
from app.logging_config import get_logger

log = get_logger(__name__)


@dataclass
class RiskDecision:
    allowed: bool
    blocking_rule: str | None = None
    risk_event_type: RiskEventType | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class RiskContext:
    """
    All inputs the risk manager needs to evaluate a potential trade.
    Callers must populate every field; use None when data is unavailable.
    """
    # Signal / scoring
    signal_id: int
    final_score: float

    # Coin metadata
    coin_id: int
    contract_address: str
    coin_launched_at: datetime | None
    last_traded_at: datetime | None           # Last time we opened a position on this coin
    is_blacklisted: bool = False

    # Live market state (from latest snapshot)
    liquidity_usd: float | None = None
    slippage_bps: int | None = None

    # Portfolio state (queried by caller before constructing this)
    daily_realised_loss_usd: float = 0.0     # Positive number = loss
    open_position_count: int = 0


class RiskManager:
    """
    Stateless risk evaluator.
    Instantiate once; call evaluate() for every candidate trade.
    All thresholds come from settings so they are environment-configurable.
    """

    def evaluate(self, ctx: RiskContext, kill_switch: bool = False) -> RiskDecision:
        """
        Evaluate all risk rules for the given context.
        Returns on the first blocking rule; advisory notes are cumulative.
        """
        notes: list[str] = []

        # ── Rule 1: Kill switch ────────────────────────────────────────────────
        if kill_switch:
            return RiskDecision(
                allowed=False,
                blocking_rule="kill_switch_active",
                risk_event_type=RiskEventType.KILL_SWITCH,
            )

        # ── Rule 2: Signal confidence ──────────────────────────────────────────
        if ctx.final_score < settings.score_paper_trade_threshold:
            return RiskDecision(
                allowed=False,
                blocking_rule=f"score_too_low ({ctx.final_score:.1f} < {settings.score_paper_trade_threshold})",
                risk_event_type=RiskEventType.LOW_CONFIDENCE,
            )

        # ── Rule 3: Per-coin cooldown ──────────────────────────────────────────
        COOLDOWN_MINUTES = 30  # TODO: make configurable in settings (Phase 4)
        if ctx.last_traded_at is not None:
            last = ctx.last_traded_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60
            if elapsed < COOLDOWN_MINUTES:
                remaining = COOLDOWN_MINUTES - elapsed
                return RiskDecision(
                    allowed=False,
                    blocking_rule=f"coin_cooldown ({remaining:.0f}m remaining)",
                    risk_event_type=RiskEventType.COIN_COOLDOWN,
                )

        # ── Rule 4: New coin lockout ───────────────────────────────────────────
        if ctx.coin_launched_at is not None:
            launched = ctx.coin_launched_at
            if launched.tzinfo is None:
                launched = launched.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - launched).total_seconds()
            if age_seconds < settings.no_trade_after_launch_seconds:
                remaining = settings.no_trade_after_launch_seconds - age_seconds
                return RiskDecision(
                    allowed=False,
                    blocking_rule=f"new_coin_lockout ({remaining:.0f}s remaining)",
                    risk_event_type=RiskEventType.NEW_COIN_LOCKOUT,
                )

        # ── Rule 5: Liquidity ──────────────────────────────────────────────────
        if ctx.liquidity_usd is None or ctx.liquidity_usd < settings.min_liquidity_usd:
            liq_str = f"${ctx.liquidity_usd:,.0f}" if ctx.liquidity_usd else "unknown"
            return RiskDecision(
                allowed=False,
                blocking_rule=f"insufficient_liquidity ({liq_str} < ${settings.min_liquidity_usd:,.0f})",
                risk_event_type=RiskEventType.LOW_LIQUIDITY,
            )

        # ── Rule 6: Slippage ───────────────────────────────────────────────────
        if ctx.slippage_bps is not None and ctx.slippage_bps > settings.max_slippage_bps:
            return RiskDecision(
                allowed=False,
                blocking_rule=f"slippage_too_high ({ctx.slippage_bps}bps > {settings.max_slippage_bps}bps)",
                risk_event_type=RiskEventType.HIGH_SLIPPAGE,
            )

        # ── Rule 7: Daily loss limit ───────────────────────────────────────────
        if ctx.daily_realised_loss_usd >= settings.max_daily_loss_usd:
            return RiskDecision(
                allowed=False,
                blocking_rule=f"daily_loss_limit (${ctx.daily_realised_loss_usd:.2f} >= ${settings.max_daily_loss_usd:.2f})",
                risk_event_type=RiskEventType.DAILY_LOSS_LIMIT,
            )

        # ── Rule 8: Concurrent position cap ───────────────────────────────────
        if ctx.open_position_count >= settings.max_concurrent_positions:
            return RiskDecision(
                allowed=False,
                blocking_rule=f"concurrent_position_limit ({ctx.open_position_count} >= {settings.max_concurrent_positions})",
                risk_event_type=RiskEventType.CONCURRENT_POSITION_LIMIT,
            )

        # ── Rule 9: Blacklist ──────────────────────────────────────────────────
        if ctx.is_blacklisted:
            return RiskDecision(
                allowed=False,
                blocking_rule="creator_or_coin_blacklisted",
                risk_event_type=RiskEventType.BLACKLISTED,
            )

        # ── Advisory notes (non-blocking) ─────────────────────────────────────
        if ctx.slippage_bps is not None and ctx.slippage_bps > settings.max_slippage_bps // 2:
            notes.append(f"elevated_slippage ({ctx.slippage_bps}bps)")

        remaining_loss_budget = settings.max_daily_loss_usd - ctx.daily_realised_loss_usd
        if remaining_loss_budget < settings.max_daily_loss_usd * 0.25:
            notes.append(f"near_daily_loss_limit (${remaining_loss_budget:.0f} remaining)")

        log.info(
            "risk_check_passed",
            signal_id=ctx.signal_id,
            coin_id=ctx.coin_id,
            score=ctx.final_score,
            notes=notes,
        )
        return RiskDecision(allowed=True, notes=notes)


# ── Singleton ──────────────────────────────────────────────────────────────────
_risk_manager: RiskManager | None = None


def get_risk_manager() -> RiskManager:
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager()
    return _risk_manager
