"""
app/bot/tools.py
─────────────────────────────────────────────────────────────────────────────
Tool execution layer for assistant.

Maps OpenAI tool calls to deterministic backend services:
  - Creator tracking (add/list tracked creators)
  - Signal queries (recent signals, explanations)
  - Coin market data (live prices, liquidity)
  - Trade management (preview, execute, position tracking)
  - Wallet linking (secure flow initiation)
  - User preferences (get/set)

All tool execution is:
  - Database-backed (persisted)
  - Routed through domain services (not autonomous)
  - Logged and auditable
  - Gated by safety checks (risk controls, feature flags)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import AsyncSessionLocal
from app.db.models import (
    CreatorWatchMode,
    TrackedCreator,
    UserPreferences,
    Recommendation,
)
from app.db.repositories.creator_tracking import TrackedCreatorRepository
from app.db.repositories.signals import SignalRepository
from app.db.repositories.positions import PaperPositionRepository
from app.db.repositories.coins import ZoraCoinRepository, CoinMarketSnapshotRepository
from app.risk import check_trade_allowed
from app.services.wallet_linking import create_link_session

log = logging.getLogger(__name__)


# ── Tool execution router ──────────────────────────────────────────────────────

class ToolExecutor:
    """Execute tool calls from OpenAI assistant."""

    def __init__(self, telegram_user_id: int, session: AsyncSession):
        self.telegram_user_id = telegram_user_id
        self.session = session

    async def execute(self, tool_name: str, tool_args: dict) -> dict:
        """
        Execute a tool and return result.

        Returns:
            {"success": bool, "data": Any} or {"success": False, "error": str}
        """
        try:
            if tool_name == "track_creator":
                return await self.track_creator(tool_args)
            elif tool_name == "list_tracked_creators":
                return await self.list_tracked_creators(tool_args)
            elif tool_name == "get_zora_signals":
                return await self.get_zora_signals(tool_args)
            elif tool_name == "explain_signal":
                return await self.explain_signal(tool_args)
            elif tool_name == "get_coin_market_state":
                return await self.get_coin_market_state(tool_args)
            elif tool_name == "preview_trade":
                return await self.preview_trade(tool_args)
            elif tool_name == "execute_trade":
                return await self.execute_trade(tool_args)
            elif tool_name == "start_wallet_link":
                return await self.start_wallet_link(tool_args)
            elif tool_name == "check_wallet_link_status":
                return await self.check_wallet_link_status(tool_args)
            elif tool_name == "get_position_status":
                return await self.get_position_status(tool_args)
            elif tool_name == "get_user_preferences":
                return await self.get_user_preferences(tool_args)
            elif tool_name == "update_user_preferences":
                return await self.update_user_preferences(tool_args)
            else:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}
        except Exception as exc:
            log.exception("tool_execution_error", tool_name=tool_name, exc_info=True)
            return {"success": False, "error": str(exc)}

    # ── Creator Tracking ───────────────────────────────────────────────────────

    async def track_creator(self, args: dict) -> dict:
        """Track a creator by X username."""
        x_username = args.get("x_username", "").strip()
        mode = args.get("mode", "hybrid")

        if not x_username:
            return {"success": False, "error": "x_username is required"}

        # Validate mode
        valid_modes = {e.value for e in CreatorWatchMode}
        if mode not in valid_modes:
            return {"success": False, "error": f"Invalid mode: {mode}"}

        repo = TrackedCreatorRepository(self.session)

        # Check if already tracking
        existing = await repo.get_by_user_and_handle(self.telegram_user_id, x_username)
        if existing:
            return {
                "success": True,
                "data": {
                    "message": f"Already tracking @{existing.x_username}",
                    "mode": existing.mode.value,
                },
            }

        # Create new tracked creator (minimal for now — can enrich via SocialData later)
        tracked = TrackedCreator(
            telegram_user_id=self.telegram_user_id,
            x_user_id=x_username,  # Placeholder; would resolve from SocialData in full impl
            x_username=x_username,
            mode=CreatorWatchMode(mode),
            is_active=True,
        )
        self.session.add(tracked)
        await self.session.commit()

        return {
            "success": True,
            "data": {
                "message": f"✅ Now tracking @{x_username} in {mode} mode",
                "creator": x_username,
                "mode": mode,
            },
        }

    async def list_tracked_creators(self, args: dict) -> dict:
        """List all creators being tracked by this user."""
        repo = TrackedCreatorRepository(self.session)
        creators = await repo.get_active_for_user(self.telegram_user_id)

        return {
            "success": True,
            "data": {
                "count": len(creators),
                "creators": [
                    {
                        "username": c.x_username,
                        "mode": c.mode.value,
                        "tracked_since": c.created_at.isoformat() if c.created_at else None,
                    }
                    for c in creators
                ],
            },
        }

    # ── Signal Queries ────────────────────────────────────────────────────────

    async def get_zora_signals(self, args: dict) -> dict:
        """Get recent Zora signals."""
        hours = args.get("hours", 24)
        min_score = args.get("min_score", 50)
        limit = 10

        repo = SignalRepository(self.session)
        signals = await repo.get_recent(limit=limit)

        # Filter by score
        filtered = [s for s in signals if s.final_score >= min_score]

        return {
            "success": True,
            "data": {
                "count": len(filtered),
                "signals": [
                    {
                        "id": s.id,
                        "coin_symbol": s.coin.symbol if s.coin else s.post.text[:20],
                        "score": s.final_score,
                        "recommendation": s.recommendation.value,
                        "created_at": s.created_at.isoformat(),
                    }
                    for s in filtered
                ],
            },
        }

    async def explain_signal(self, args: dict) -> dict:
        """Explain why a signal was scored."""
        signal_id = args.get("signal_id")
        if not signal_id:
            return {"success": False, "error": "signal_id is required"}

        repo = SignalRepository(self.session)
        signal = await repo.get(signal_id)
        if not signal:
            return {"success": False, "error": f"Signal {signal_id} not found"}

        return {
            "success": True,
            "data": {
                "signal_id": signal.id,
                "coin": signal.coin.symbol if signal.coin else "Unknown",
                "deterministic_score": signal.deterministic_score,
                "llm_score": signal.llm_score,
                "final_score": signal.final_score,
                "recommendation": signal.recommendation.value,
                "risk_notes": signal.risk_notes or "None",
                "explanation": self._explain_score_breakdown(signal),
            },
        }

    @staticmethod
    def _explain_score_breakdown(signal) -> str:
        """Generate human-readable explanation of score."""
        lines = []
        lines.append(f"📊 Score Breakdown:")
        lines.append(f"  Deterministic: {signal.deterministic_score:.0f}/100")
        if signal.llm_score:
            lines.append(f"  LLM Assessment: {signal.llm_score:.0f}/100")
        lines.append(f"  Final: {signal.final_score:.0f}/100")

        if signal.recommendation == Recommendation.IGNORE:
            lines.append("\n❌ IGNORE — Score below watch threshold")
        elif signal.recommendation == Recommendation.WATCH:
            lines.append("\n👀 WATCH — Worth monitoring")
        elif signal.recommendation == Recommendation.ALERT:
            lines.append("\n⚠️  ALERT — Strong signal, consider trading")
        elif signal.recommendation in (
            Recommendation.PAPER_TRADE,
            Recommendation.LIVE_TRADE_READY,
        ):
            lines.append("\n🚀 TRADE READY — High confidence signal")

        return "\n".join(lines)

    async def get_coin_market_state(self, args: dict) -> dict:
        """Get current market data for a coin."""
        coin_symbol = args.get("coin_symbol", "").strip()
        if not coin_symbol:
            return {"success": False, "error": "coin_symbol is required"}

        coin_repo = ZoraCoinRepository(self.session)
        coin = await coin_repo.get_by_symbol(coin_symbol)
        if not coin:
            return {"success": False, "error": f"Coin {coin_symbol} not found"}

        # Get latest market snapshot
        snapshot_repo = CoinMarketSnapshotRepository(self.session)
        latest_snapshot = await snapshot_repo.get_latest_for_coin(coin.id)

        return {
            "success": True,
            "data": {
                "symbol": coin.symbol,
                "price_usd": latest_snapshot.price_usd if latest_snapshot else None,
                "liquidity_usd": latest_snapshot.liquidity_usd if latest_snapshot else None,
                "volume_5m": latest_snapshot.volume_5m_usd if latest_snapshot else None,
                "market_cap_usd": latest_snapshot.market_cap_usd if latest_snapshot else None,
                "holder_count": latest_snapshot.holder_count if latest_snapshot else None,
                "snapshot_age_seconds": (
                    int((datetime.utcnow() - latest_snapshot.captured_at).total_seconds())
                    if latest_snapshot
                    else None
                ),
            },
        }

    # ── Trade Management ───────────────────────────────────────────────────────

    async def preview_trade(self, args: dict) -> dict:
        """Preview a trade without executing."""
        coin_symbol = args.get("coin_symbol", "").strip()
        action = args.get("action", "buy")  # buy | sell
        amount_usd = float(args.get("amount_usd", 0))

        if not coin_symbol or not action or amount_usd <= 0:
            return {
                "success": False,
                "error": "coin_symbol, action, and amount_usd (>0) required",
            }

        if action not in ("buy", "sell"):
            return {"success": False, "error": f"Invalid action: {action}"}

        coin_repo = ZoraCoinRepository(self.session)
        coin = await coin_repo.get_by_symbol(coin_symbol)
        if not coin:
            return {"success": False, "error": f"Coin {coin_symbol} not found"}

        # Get market data
        snapshot_repo = CoinMarketSnapshotRepository(self.session)
        latest_snapshot = await snapshot_repo.get_latest_for_coin(coin.id)

        price_usd = latest_snapshot.price_usd if latest_snapshot else 0
        slippage_bps = 150  # Estimate 1.5%
        fees_bps = 30  # Estimate 0.3% + base fee

        return {
            "success": True,
            "data": {
                "coin": coin_symbol,
                "action": action,
                "amount_usd": amount_usd,
                "price_usd": price_usd,
                "estimated_slippage_bps": slippage_bps,
                "estimated_slippage_pct": slippage_bps / 100,
                "estimated_fees_bps": fees_bps,
                "estimated_fees_usd": (amount_usd * fees_bps) / 10000,
                "total_cost_usd": amount_usd + (amount_usd * (slippage_bps + fees_bps)) / 10000,
                "message": f"Preview: {action.upper()} {amount_usd:.2f} USD of {coin_symbol}\nEstimated total cost: ${amount_usd * (1 + (slippage_bps + fees_bps) / 10000):.2f}",
            },
        }

    async def execute_trade(self, args: dict) -> dict:
        """Execute a real trade (gated by risk controls and wallet linking)."""
        coin_symbol = args.get("coin_symbol", "").strip()
        action = args.get("action", "buy").lower()
        amount_usd = float(args.get("amount_usd", 0))

        if not all([coin_symbol, action, amount_usd]):
            return {"success": False, "error": "coin_symbol, action, amount_usd required"}

        if action not in ("buy", "sell"):
            return {"success": False, "error": f"Invalid action: {action}"}

        # Phase 3: Run comprehensive risk checks
        risk_check = await check_trade_allowed(
            session=self.session,
            telegram_user_id=self.telegram_user_id,
            coin_symbol=coin_symbol,
            action=action,
            amount_usd=amount_usd,
            slippage_bps=150,  # Default estimate
        )

        if not risk_check.allowed:
            return {
                "success": False,
                "error": risk_check.reason,
                "blocked_reason": "risk_check_failed",
            }

        # TODO: Phase 3+ - Actually execute trade to smart contract
        # For now, just indicate it would execute
        return {
            "success": True,
            "message": f"✅ Trade would execute (Phase 3 implementation pending)\n"
            f"Action: {action.upper()}\n"
            f"Coin: {coin_symbol}\n"
            f"Amount: ${amount_usd}",
            "status": "pending_execution",
        }

    # ── Wallet Linking ────────────────────────────────────────────────────────

    async def start_wallet_link(self, args: dict) -> dict:
        """Initiate wallet linking flow."""
        # This will call the walletlinking service
        try:
            session_token = await create_link_session(self.telegram_user_id)
            link_url = f"{settings.wallet_link_base_url}/wallet-link/{session_token}"

            return {
                "success": True,
                "data": {
                    "link": link_url,
                    "expires_seconds": settings.wallet_nonce_ttl_seconds,
                    "message": f"🔗 Click to securely link your wallet:\n{link_url}",
                },
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def check_wallet_link_status(self, args: dict) -> dict:
        """Check if wallet is linked."""
        # TODO: Implement in Phase 3 when wallet link verification is complete
        return {
            "success": True,
            "data": {
                "wallet_linked": False,
                "trading_enabled": False,
                "message": "Wallet not linked yet. Use start_wallet_link to begin.",
            },
        }

    # ── Position Management ────────────────────────────────────────────────────

    async def get_position_status(self, args: dict) -> dict:
        """Get current positions."""
        repo = PaperPositionRepository(self.session)
        positions = await repo.get_open()

        return {
            "success": True,
            "data": {
                "count_open": len(positions),
                "positions": [
                    {
                        "id": p.id,
                        "coin": p.coin.symbol if p.coin else "Unknown",
                        "size_usd": p.size_usd,
                        "entry_price": p.entry_price_usd,
                        "opened_at": p.opened_at.isoformat(),
                    }
                    for p in positions
                ],
            },
        }

    # ── User Preferences ───────────────────────────────────────────────────────

    async def get_user_preferences(self, args: dict) -> dict:
        """Get user preferences."""
        stmt = select(UserPreferences).where(
            UserPreferences.telegram_user_id == self.telegram_user_id
        )
        result = await self.session.execute(stmt)
        prefs_list = result.scalars().all()

        return {
            "success": True,
            "data": {
                "preferences": {p.preference_key: p.preference_value for p in prefs_list}
            },
        }

    async def update_user_preferences(self, args: dict) -> dict:
        """Update user preferences."""
        preferences = args.get("preferences", {})

        for key, value in preferences.items():
            # Upsert preference
            stmt = select(UserPreferences).where(
                UserPreferences.telegram_user_id == self.telegram_user_id,
                UserPreferences.preference_key == key,
            )
            result = await self.session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                existing.preference_value = str(value)
            else:
                pref = UserPreferences(
                    telegram_user_id=self.telegram_user_id,
                    preference_key=key,
                    preference_value=str(value),
                )
                self.session.add(pref)

        await self.session.commit()

        return {
            "success": True,
            "data": {"message": "Preferences updated", "count": len(preferences)},
        }


# ── Helper for using executor ──────────────────────────────────────────────────

async def execute_tool(
    telegram_user_id: int,
    tool_name: str,
    tool_args: dict,
) -> dict:
    """
    Execute a tool call from OpenAI.

    Args:
        telegram_user_id: User ID
        tool_name: Tool name from OpenAI
        tool_args: Parsed arguments

    Returns:
        Result dict (always has 'success' bool)
    """
    async with AsyncSessionLocal() as session:
        executor = ToolExecutor(telegram_user_id, session)
        return await executor.execute(tool_name, tool_args)
