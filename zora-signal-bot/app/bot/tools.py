"""Deterministic tool execution layer for the conversational assistant."""

from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import AsyncSessionLocal
from app.db.models import (
    CreatorWatchMode,
    ExecutionAuditLog,
    LiveOrder,
    Recommendation,
    ToolCallAuditLog,
    TrackedCreator,
    TradePreview,
    UserPreferences,
)
from app.db.repositories.coins import CoinMarketSnapshotRepository, ZoraCoinRepository
from app.db.repositories.creator_tracking import TrackedCreatorRepository
from app.db.repositories.positions import PaperPositionRepository
from app.db.repositories.signals import SignalRepository
from app.risk import check_trade_allowed
from app.services.wallet_linking import create_link_session
from app.trading.paper_engine import get_paper_engine

log = logging.getLogger(__name__)


async def _resolve(value):
    if inspect.isawaitable(value):
        return await value
    return value


class ToolExecutor:
    """Execute assistant tool calls against deterministic backend services."""

    def __init__(self, telegram_user_id: int, session: AsyncSession):
        self.telegram_user_id = telegram_user_id
        self.session = session

    async def _session_add(self, obj) -> None:
        result = self.session.add(obj)
        if inspect.isawaitable(result):
            await result

    async def execute(self, tool_name: str, tool_args: dict) -> dict:
        tool_map = {
            "track_creator": self.track_creator,
            "list_tracked_creators": self.list_tracked_creators,
            "classify_post_intent": self.classify_post_intent,
            "find_zora_candidates": self.find_zora_candidates,
            "get_zora_signals": self.get_zora_signals,
            "explain_signal": self.explain_signal,
            "get_coin_market_state": self.get_coin_market_state,
            "preview_trade": self.preview_trade,
            "preview_trade_signal": self.preview_trade,
            "execute_trade": self.execute_trade,
            "execute_trade_signal": self.execute_trade,
            "start_wallet_link": self.start_wallet_link,
            "check_wallet_link_status": self.check_wallet_link_status,
            "get_position_status": self.get_position_status,
            "close_position": self.close_position,
            "get_user_preferences": self.get_user_preferences,
            "update_user_preferences": self.update_user_preferences,
        }
        handler = tool_map.get(tool_name)
        if handler is None:
            result = {"success": False, "error": f"Unknown tool: {tool_name}"}
            await self._log_tool_call(tool_name, tool_args, result)
            return result

        try:
            result = await handler(tool_args)
        except Exception as exc:
            log.exception("tool_execution_error", tool_name=tool_name, exc_info=True)
            result = {"success": False, "error": str(exc)}

        await self._log_tool_call(tool_name, tool_args, result)
        return result

    async def _log_tool_call(self, tool_name: str, tool_args: dict, result: dict) -> None:
        await self._session_add(
            ToolCallAuditLog(
                telegram_user_id=self.telegram_user_id,
                tool_name=tool_name,
                arguments_json=json.dumps(tool_args, default=str),
                result_json=json.dumps(result, default=str),
                success=bool(result.get("success")),
            )
        )
        await self.session.flush()

    async def _record_execution_audit(
        self,
        action: str,
        status: str,
        *,
        coin_symbol: str | None = None,
        details: dict | None = None,
    ) -> None:
        await self._session_add(
            ExecutionAuditLog(
                telegram_user_id=self.telegram_user_id,
                action=action,
                status=status,
                coin_symbol=coin_symbol,
                details_json=json.dumps(details or {}, default=str),
            )
        )
        await self.session.flush()

    async def track_creator(self, args: dict) -> dict:
        x_username = args.get("x_username", "").strip()
        mode = args.get("mode", "hybrid")
        if not x_username:
            return {"success": False, "error": "x_username is required"}
        valid_modes = {e.value for e in CreatorWatchMode}
        if mode not in valid_modes:
            return {"success": False, "error": f"Invalid mode: {mode}"}

        repo = TrackedCreatorRepository(self.session)
        existing = await repo.get_by_user_and_handle(self.telegram_user_id, x_username)
        if existing:
            return {
                "success": True,
                "data": {
                    "message": f"Already tracking @{existing.x_username}",
                    "mode": existing.mode.value,
                },
            }

        tracked = TrackedCreator(
            telegram_user_id=self.telegram_user_id,
            x_user_id=x_username,
            x_username=x_username,
            mode=CreatorWatchMode(mode),
            is_active=True,
        )
        await self._session_add(tracked)
        await self.session.commit()
        return {
            "success": True,
            "data": {
                "message": f"Now tracking @{x_username} in {mode} mode",
                "creator": x_username,
                "mode": mode,
            },
        }

    async def list_tracked_creators(self, args: dict) -> dict:
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

    async def classify_post_intent(self, args: dict) -> dict:
        text = (args.get("text") or args.get("post_text") or "").strip()
        if not text:
            return {"success": False, "error": "text is required"}
        lowered = text.lower()
        bullish_terms = ("buy", "ape", "bullish", "send it", "long")
        bearish_terms = ("sell", "avoid", "rug", "bearish", "short")
        if any(term in lowered for term in bullish_terms):
            intent = "bullish"
            confidence = 70
        elif any(term in lowered for term in bearish_terms):
            intent = "bearish"
            confidence = 70
        else:
            intent = "neutral"
            confidence = 45
        return {
            "success": True,
            "data": {"intent": intent, "confidence": confidence, "used_llm": False},
        }

    async def find_zora_candidates(self, args: dict) -> dict:
        query = (args.get("query") or args.get("creator") or args.get("text") or "").strip()
        signals = await SignalRepository(self.session).get_recent(limit=5)
        candidates = []
        for signal in signals:
            symbol = signal.coin.symbol if signal.coin else None
            post_text = signal.post.text if signal.post and signal.post.text else ""
            if not symbol:
                continue
            if query and query.lower() not in symbol.lower() and query.lower() not in post_text.lower():
                continue
            candidates.append(
                {
                    "coin_symbol": symbol,
                    "signal_id": signal.id,
                    "rank_score": signal.final_score,
                    "reason": "Matched existing scored signal context",
                }
            )
        return {
            "success": True,
            "data": {"query": query or None, "count": len(candidates), "candidates": candidates},
        }

    async def get_zora_signals(self, args: dict) -> dict:
        min_score = args.get("min_score", 50)
        signals = await SignalRepository(self.session).get_recent(limit=10)
        filtered = [s for s in signals if s.final_score >= min_score]
        return {
            "success": True,
            "data": {
                "count": len(filtered),
                "signals": [
                    {
                        "id": s.id,
                        "coin_symbol": s.coin.symbol if s.coin else (s.post.text[:20] if s.post and s.post.text else "Unknown"),
                        "score": s.final_score,
                        "recommendation": s.recommendation.value,
                        "created_at": s.created_at.isoformat(),
                    }
                    for s in filtered
                ],
            },
        }

    async def explain_signal(self, args: dict) -> dict:
        signal_id = args.get("signal_id")
        if not signal_id:
            return {"success": False, "error": "signal_id is required"}
        signal = await SignalRepository(self.session).get(signal_id)
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
        lines = [
            "\U0001F4CA Score Breakdown:",
            f"  Deterministic: {signal.deterministic_score:.0f}/100",
        ]
        if signal.llm_score:
            lines.append(f"  LLM Assessment: {signal.llm_score:.0f}/100")
        lines.append(f"  Final: {signal.final_score:.0f}/100")
        if signal.recommendation == Recommendation.IGNORE:
            lines.append("\nIGNORE - Score below watch threshold")
        elif signal.recommendation == Recommendation.WATCH:
            lines.append("\nWATCH - Worth monitoring")
        elif signal.recommendation == Recommendation.ALERT:
            lines.append("\nALERT - Strong signal, consider trading")
        else:
            lines.append("\nTRADE READY - High confidence signal")
        return "\n".join(lines)

    async def get_coin_market_state(self, args: dict) -> dict:
        coin_symbol = args.get("coin_symbol", "").strip()
        if not coin_symbol:
            return {"success": False, "error": "coin_symbol is required"}
        coin = await ZoraCoinRepository(self.session).get_by_symbol(coin_symbol)
        if not coin:
            return {"success": False, "error": f"Coin {coin_symbol} not found"}
        latest_snapshot = await CoinMarketSnapshotRepository(self.session).get_latest_for_coin(coin.id)
        return {
            "success": True,
            "data": {
                "symbol": coin.symbol,
                "price_usd": latest_snapshot.price_usd if latest_snapshot else None,
                "liquidity_usd": latest_snapshot.liquidity_usd if latest_snapshot else None,
                "volume_5m": latest_snapshot.volume_5m_usd if latest_snapshot else None,
                "market_cap_usd": latest_snapshot.market_cap_usd if latest_snapshot else None,
                "holder_count": latest_snapshot.holder_count if latest_snapshot else None,
                "snapshot_age_seconds": int((datetime.utcnow() - latest_snapshot.captured_at).total_seconds()) if latest_snapshot else None,
            },
        }

    async def preview_trade(self, args: dict) -> dict:
        coin_symbol = args.get("coin_symbol", "").strip()
        action = args.get("action", "buy")
        amount_usd = float(args.get("amount_usd", 0))
        if not coin_symbol or not action or amount_usd <= 0:
            return {"success": False, "error": "coin_symbol, action, and amount_usd (>0) required"}
        if action not in ("buy", "sell"):
            return {"success": False, "error": f"Invalid action: {action}"}
        coin = await ZoraCoinRepository(self.session).get_by_symbol(coin_symbol)
        if not coin:
            return {"success": False, "error": f"Coin {coin_symbol} not found"}
        latest_snapshot = await CoinMarketSnapshotRepository(self.session).get_latest_for_coin(coin.id)
        price_usd = latest_snapshot.price_usd if latest_snapshot else 0
        slippage_bps = 150
        fees_bps = 30
        total_cost_usd = amount_usd + (amount_usd * (slippage_bps + fees_bps)) / 10000
        preview = {
            "coin": coin_symbol,
            "action": action,
            "amount_usd": amount_usd,
            "price_usd": price_usd,
            "estimated_slippage_bps": slippage_bps,
            "estimated_slippage_pct": slippage_bps / 100,
            "estimated_fees_bps": fees_bps,
            "estimated_fees_usd": (amount_usd * fees_bps) / 10000,
            "total_cost_usd": total_cost_usd,
            "message": f"Preview: {action.upper()} {amount_usd:.2f} USD of {coin_symbol}\nEstimated total cost: ${total_cost_usd:.2f}",
        }
        await self._session_add(
            TradePreview(
                telegram_user_id=self.telegram_user_id,
                coin_symbol=coin_symbol,
                action=action,
                amount_usd=amount_usd,
                price_usd=price_usd,
                estimated_slippage_bps=slippage_bps,
                estimated_fees_usd=preview["estimated_fees_usd"],
                total_cost_usd=total_cost_usd,
                preview_json=json.dumps(preview, default=str),
            )
        )
        await self._record_execution_audit("trade_preview", "previewed", coin_symbol=coin_symbol, details=preview)
        await self.session.commit()
        return {"success": True, "data": preview}

    async def execute_trade(self, args: dict) -> dict:
        coin_symbol = args.get("coin_symbol", "").strip()
        action = args.get("action", "buy").lower()
        amount_usd = float(args.get("amount_usd", 0))
        if not all([coin_symbol, action, amount_usd]):
            return {"success": False, "error": "coin_symbol, action, amount_usd required"}
        if action not in ("buy", "sell"):
            return {"success": False, "error": f"Invalid action: {action}"}
        risk_check = await check_trade_allowed(
            session=self.session,
            telegram_user_id=self.telegram_user_id,
            coin_symbol=coin_symbol,
            action=action,
            amount_usd=amount_usd,
            slippage_bps=150,
        )
        if not risk_check.allowed:
            await self._record_execution_audit(
                "trade_execute",
                "blocked",
                coin_symbol=coin_symbol,
                details={"reason": risk_check.reason, "action": action, "amount_usd": amount_usd},
            )
            await self.session.commit()
            error_message = risk_check.reason
            if "wallet not linked" in error_message.lower() and "wallet linking" not in error_message.lower():
                error_message = f"Wallet linking required. {risk_check.reason}"
            return {"success": False, "error": error_message, "blocked_reason": "risk_check_failed"}
        live_order = LiveOrder(
            telegram_user_id=self.telegram_user_id,
            coin_symbol=coin_symbol,
            action=action,
            amount_usd=amount_usd,
            status="pending_execution",
        )
        await self._session_add(live_order)
        await self._record_execution_audit(
            "trade_execute",
            "pending_execution",
            coin_symbol=coin_symbol,
            details={"action": action, "amount_usd": amount_usd},
        )
        await self.session.commit()
        return {
            "success": True,
            "data": {
                "message": f"Trade queued for execution review: {action.upper()} ${amount_usd:.2f} of {coin_symbol}",
                "status": "pending_execution",
                "order_id": live_order.id,
            },
        }

    async def start_wallet_link(self, args: dict) -> dict:
        if not settings.enable_wallet_linking:
            return {"success": False, "error": "Wallet linking is disabled. Set ENABLE_WALLET_LINKING=true."}
        try:
            link_url = await create_link_session(self.session, self.telegram_user_id)
            await self._record_execution_audit("wallet_link_start", "created", details={"link_url": link_url})
            await self.session.commit()
            return {
                "success": True,
                "data": {
                    "link": link_url,
                    "expires_seconds": settings.wallet_nonce_ttl_seconds,
                    "message": f"Click to securely link your wallet:\n{link_url}",
                },
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def check_wallet_link_status(self, args: dict) -> dict:
        return {
            "success": True,
            "data": {
                "wallet_linked": False,
                "trading_enabled": False,
                "message": "Wallet not linked yet. Use start_wallet_link to begin.",
            },
        }

    async def get_position_status(self, args: dict) -> dict:
        positions = await PaperPositionRepository(self.session).get_open()
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

    async def close_position(self, args: dict) -> dict:
        position_id = int(args.get("position_id", 0))
        if position_id <= 0:
            return {"success": False, "error": "position_id is required"}
        repo = PaperPositionRepository(self.session)
        position = await repo.get(position_id)
        if position is None:
            return {"success": False, "error": f"Position {position_id} not found"}
        latest_snapshot = await CoinMarketSnapshotRepository(self.session).get_latest_for_coin(position.coin_id)
        if latest_snapshot is None or latest_snapshot.price_usd is None:
            return {"success": False, "error": "No price data available to close position"}
        result = await get_paper_engine().close_position(self.session, position_id, latest_snapshot.price_usd, "MANUAL")
        await self._record_execution_audit(
            "close_position",
            "closed" if result.success else "failed",
            coin_symbol=position.coin.symbol if position.coin else None,
            details={"position_id": position_id, "pnl_usd": result.pnl_usd, "pnl_pct": result.pnl_pct},
        )
        await self.session.commit()
        if not result.success:
            return {"success": False, "error": result.message}
        return {
            "success": True,
            "data": {
                "position_id": position_id,
                "pnl_usd": result.pnl_usd,
                "pnl_pct": result.pnl_pct,
                "exit_reason": result.exit_reason,
                "message": f"Closed position #{position_id} at ${latest_snapshot.price_usd:.6f}",
            },
        }

    async def get_user_preferences(self, args: dict) -> dict:
        stmt = select(UserPreferences).where(UserPreferences.telegram_user_id == self.telegram_user_id)
        result = await self.session.execute(stmt)
        scalars = await _resolve(result.scalars())
        prefs_list = await _resolve(scalars.all())
        return {"success": True, "data": {"preferences": {p.preference_key: p.preference_value for p in prefs_list}}}

    async def update_user_preferences(self, args: dict) -> dict:
        preferences = args.get("preferences", {})
        for key, value in preferences.items():
            stmt = select(UserPreferences).where(
                UserPreferences.telegram_user_id == self.telegram_user_id,
                UserPreferences.preference_key == key,
            )
            result = await self.session.execute(stmt)
            existing = await _resolve(result.scalar_one_or_none())
            if existing:
                existing.preference_value = str(value)
            else:
                await self._session_add(
                    UserPreferences(
                        telegram_user_id=self.telegram_user_id,
                        preference_key=key,
                        preference_value=str(value),
                    )
                )
        await self.session.commit()
        return {"success": True, "data": {"message": "Preferences updated", "count": len(preferences)}}


async def execute_tool(telegram_user_id: int, tool_name: str, tool_args: dict) -> dict:
    async with AsyncSessionLocal() as session:
        executor = ToolExecutor(telegram_user_id, session)
        return await executor.execute(tool_name, tool_args)



