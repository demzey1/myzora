"""Telegram inline buttons and guided callback flows."""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.tools import execute_tool
from app.config import settings
from app.db.base import AsyncSessionLocal
from app.db.repositories.creator_tracking import TrackedCreatorRepository
from app.logging_config import get_logger

log = get_logger(__name__)

TRADE_CONFIRM_PREFIX = "trade_confirm"
TRADE_CANCEL_PREFIX = "trade_cancel"
CLOSE_POSITION_PREFIX = "close_position"
TRACK_CREATOR_PREFIX = "track_creator_confirm"
CREATOR_PREFIX = "creator"
NAV_PREFIX = "nav"
SIGNAL_PREFIX = "signal"


def _cb(payload: str) -> str:
    return payload[:64]


def _parse_callback_data(data: str) -> tuple[str, dict[str, str]]:
    parts = data.split("|")
    prefix = parts[0]
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key] = value
    return prefix, params


def make_home_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Track Creator", callback_data=_cb(f"{NAV_PREFIX}|action=track_prompt")),
                InlineKeyboardButton("View Signals", callback_data=_cb(f"{NAV_PREFIX}|action=top_signals")),
            ],
            [
                InlineKeyboardButton("Link Wallet", callback_data=_cb(f"{NAV_PREFIX}|action=wallet_link")),
                InlineKeyboardButton("View Positions", callback_data=_cb(f"{NAV_PREFIX}|action=positions")),
            ],
            [
                InlineKeyboardButton("Trade Settings", callback_data=_cb(f"{NAV_PREFIX}|action=settings")),
                InlineKeyboardButton("Refresh", callback_data=_cb(f"{NAV_PREFIX}|action=home")),
            ],
        ]
    )


def make_help_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Track Creator", callback_data=_cb(f"{NAV_PREFIX}|action=track_prompt")),
                InlineKeyboardButton("View Signals", callback_data=_cb(f"{NAV_PREFIX}|action=top_signals")),
            ],
            [
                InlineKeyboardButton("Link Wallet", callback_data=_cb(f"{NAV_PREFIX}|action=wallet_link")),
                InlineKeyboardButton("View Positions", callback_data=_cb(f"{NAV_PREFIX}|action=positions")),
            ],
            [
                InlineKeyboardButton("Wallet Status", callback_data=_cb(f"{NAV_PREFIX}|action=wallet_status")),
                InlineKeyboardButton("Trade Settings", callback_data=_cb(f"{NAV_PREFIX}|action=settings")),
            ],
        ]
    )


def make_status_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("View Signals", callback_data=_cb(f"{NAV_PREFIX}|action=top_signals")),
                InlineKeyboardButton("View Positions", callback_data=_cb(f"{NAV_PREFIX}|action=positions")),
            ],
            [
                InlineKeyboardButton("Link Wallet", callback_data=_cb(f"{NAV_PREFIX}|action=wallet_link")),
                InlineKeyboardButton("Trade Settings", callback_data=_cb(f"{NAV_PREFIX}|action=settings")),
            ],
            [
                InlineKeyboardButton("Refresh", callback_data=_cb(f"{NAV_PREFIX}|action=home")),
            ],
        ]
    )


def make_positions_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("View Signals", callback_data=_cb(f"{NAV_PREFIX}|action=top_signals")),
                InlineKeyboardButton("Refresh", callback_data=_cb(f"{NAV_PREFIX}|action=positions")),
            ],
            [
                InlineKeyboardButton("Back Home", callback_data=_cb(f"{NAV_PREFIX}|action=home")),
            ],
        ]
    )


def make_settings_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("View Signals", callback_data=_cb(f"{NAV_PREFIX}|action=top_signals")),
                InlineKeyboardButton("Wallet Status", callback_data=_cb(f"{NAV_PREFIX}|action=wallet_status")),
            ],
            [
                InlineKeyboardButton("View Positions", callback_data=_cb(f"{NAV_PREFIX}|action=positions")),
                InlineKeyboardButton("Back Home", callback_data=_cb(f"{NAV_PREFIX}|action=home")),
            ],
        ]
    )


def make_creator_tracked_buttons(x_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("View Signals", callback_data=_cb(f"{NAV_PREFIX}|action=top_signals")),
                InlineKeyboardButton("Stop Tracking", callback_data=_cb(f"{CREATOR_PREFIX}|action=untrack|handle={x_username}")),
            ],
            [
                InlineKeyboardButton("Trade Settings", callback_data=_cb(f"{NAV_PREFIX}|action=settings")),
                InlineKeyboardButton("Link Wallet", callback_data=_cb(f"{NAV_PREFIX}|action=wallet_link")),
            ],
        ]
    )


def make_signals_overview_buttons(top_signal: dict[str, Any] | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if top_signal:
        signal_id = top_signal.get("id")
        coin_symbol = str(top_signal.get("coin_symbol") or "")
        rows.append(
            [
                InlineKeyboardButton(
                    "Explain Signal",
                    callback_data=_cb(f"{SIGNAL_PREFIX}|action=explain|signal_id={signal_id}"),
                ),
                InlineKeyboardButton(
                    "Preview Buy",
                    callback_data=_cb(f"{SIGNAL_PREFIX}|action=preview_buy|coin={coin_symbol}"),
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("Refresh", callback_data=_cb(f"{NAV_PREFIX}|action=top_signals")),
            InlineKeyboardButton("Back Home", callback_data=_cb(f"{NAV_PREFIX}|action=home")),
        ]
    )
    return InlineKeyboardMarkup(rows)


def make_trade_preview_buttons(
    coin_symbol: str,
    action: str,
    amount_usd: float,
) -> InlineKeyboardMarkup:
    confirm_data = f"{TRADE_CONFIRM_PREFIX}|coin={coin_symbol}|action={action}|amount={amount_usd}"
    cancel_data = f"{TRADE_CANCEL_PREFIX}|coin={coin_symbol}"

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"Confirm {action.title()}", callback_data=_cb(confirm_data)),
                InlineKeyboardButton("Cancel", callback_data=_cb(cancel_data)),
            ],
            [
                InlineKeyboardButton("Back Home", callback_data=_cb(f"{NAV_PREFIX}|action=home")),
            ],
        ]
    )


def make_position_buttons(position_id: int) -> InlineKeyboardMarkup:
    close_data = f"{CLOSE_POSITION_PREFIX}|pos_id={position_id}"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Close Position", callback_data=_cb(close_data))]]
    )


def make_wallet_link_button(link_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Open Wallet Link", url=link_url)],
            [
                InlineKeyboardButton("Check Status", callback_data=_cb(f"{NAV_PREFIX}|action=wallet_status")),
                InlineKeyboardButton("Back Home", callback_data=_cb(f"{NAV_PREFIX}|action=home")),
            ],
        ]
    )


def make_wallet_status_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Link Wallet", callback_data=_cb(f"{NAV_PREFIX}|action=wallet_link")),
                InlineKeyboardButton("Settings", callback_data=_cb(f"{NAV_PREFIX}|action=settings")),
            ],
            [
                InlineKeyboardButton("Back Home", callback_data=_cb(f"{NAV_PREFIX}|action=home")),
            ],
        ]
    )


async def handle_trade_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    try:
        _, params = _parse_callback_data(query.data or "")
        coin_symbol = params.get("coin", "").upper()
        action = params.get("action", "buy").lower()
        amount_usd = float(params.get("amount", 0))

        if not all([coin_symbol, action, amount_usd]):
            await query.answer("Trade parameters are incomplete.", show_alert=True)
            return

        result = await execute_tool(
            telegram_user_id=user_id,
            tool_name="execute_trade",
            tool_args={
                "coin_symbol": coin_symbol,
                "action": action,
                "amount_usd": amount_usd,
            },
        )

        if result.get("success"):
            await query.answer("Trade request submitted.")
            await query.edit_message_text(
                text=(
                    "<b>Trade Submitted</b>\n\n"
                    f"{result.get('data', {}).get('message', 'Your order is now in review.')}"
                ),
                parse_mode="HTML",
                reply_markup=make_home_buttons(),
            )
            return

        error_msg = result.get("error", "Trade failed")
        await query.answer(error_msg, show_alert=True)
        await query.edit_message_text(
            text=f"<b>Trade Blocked</b>\n\n{error_msg}",
            parse_mode="HTML",
            reply_markup=make_home_buttons(),
        )

    except Exception as exc:
        log.exception("trade_confirm_error", error=str(exc))
        await query.answer("Trade confirmation failed.", show_alert=True)


async def handle_trade_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    try:
        await query.answer("Cancelled.")
        await query.edit_message_text(
            text=(
                "<b>Trade Preview Cancelled</b>\n\n"
                "No action was taken. You can ask for another signal or preview any time."
            ),
            parse_mode="HTML",
            reply_markup=make_home_buttons(),
        )
    except Exception as exc:
        log.exception("trade_cancel_error", error=str(exc))


async def handle_close_position(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    try:
        _, params = _parse_callback_data(query.data or "")
        position_id = int(params.get("pos_id", 0))
        if not position_id:
            await query.answer("Position ID missing.", show_alert=True)
            return

        result = await execute_tool(
            telegram_user_id=user_id,
            tool_name="close_position",
            tool_args={"position_id": position_id},
        )
        if result.get("success"):
            await query.answer("Position closed.")
            await query.edit_message_text(
                text=result.get("data", {}).get("message", "Position closed."),
                reply_markup=make_positions_buttons(),
            )
            return

        await query.answer(result.get("error", "Close failed"), show_alert=True)
    except Exception as exc:
        log.exception("close_position_error", error=str(exc))
        await query.answer("Close failed.", show_alert=True)


async def handle_track_creator_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    try:
        _, params = _parse_callback_data(query.data or "")
        handle = params.get("handle", "").lstrip("@")
        if not handle:
            await query.answer("Creator handle missing.", show_alert=True)
            return

        result = await execute_tool(
            telegram_user_id=user_id,
            tool_name="track_creator",
            tool_args={"x_username": handle, "mode": settings.default_creator_mode},
        )
        if result.get("success"):
            await query.answer("Creator tracked.")
            await query.edit_message_text(
                text=(
                    f"Now tracking <b>@{handle}</b>.\n\n"
                    "I’ll watch for creator-linked Zora signals and explain any strong setups."
                ),
                parse_mode="HTML",
                reply_markup=make_creator_tracked_buttons(handle),
            )
            return
        await query.answer(result.get("error", "Track failed"), show_alert=True)
    except Exception as exc:
        log.exception("track_creator_confirm_error", error=str(exc))
        await query.answer("Tracking failed.", show_alert=True)


async def handle_creator_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    try:
        _, params = _parse_callback_data(query.data or "")
        action = params.get("action")
        handle = params.get("handle", "").lstrip("@")
        if action != "untrack" or not handle:
            await query.answer("Creator action unavailable.", show_alert=True)
            return

        async with AsyncSessionLocal() as session:
            repo = TrackedCreatorRepository(session)
            creator = await repo.get_by_user_and_handle(user_id, handle)
            if creator is None or not creator.is_active:
                await query.answer("Creator is not currently tracked.", show_alert=True)
                return
            creator.is_active = False
            await repo.save(creator)
            await session.commit()

        await query.answer("Tracking stopped.")
        await query.edit_message_text(
            text=(
                f"Stopped tracking <b>@{handle}</b>.\n\n"
                "You can add them again any time from chat."
            ),
            parse_mode="HTML",
            reply_markup=make_home_buttons(),
        )
    except Exception as exc:
        log.exception("creator_action_error", error=str(exc))
        await query.answer("Creator action failed.", show_alert=True)


async def handle_nav_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    try:
        _, params = _parse_callback_data(query.data or "")
        action = params.get("action")

        if action == "home":
            await query.answer()
            await query.edit_message_text(
                text=(
                    "<b>Zora Signal Bot</b>\n\n"
                    "Track creators, review strong Zora signals, explain why a coin was "
                    "flagged, and move into wallet or trade flows with safety gates on."
                ),
                parse_mode="HTML",
                reply_markup=make_home_buttons(),
            )
            return

        if action == "track_prompt":
            await query.answer()
            await query.edit_message_text(
                text=(
                    "<b>Track a Creator</b>\n\n"
                    "Send a message like <code>track @creatorname</code> and I’ll add that "
                    "creator to your watchlist."
                ),
                parse_mode="HTML",
                reply_markup=make_home_buttons(),
            )
            return

        if action == "top_signals":
            result = await execute_tool(
                telegram_user_id=user_id,
                tool_name="get_zora_signals",
                tool_args={"min_score": 60},
            )
            await query.answer("Signals refreshed.")
            await query.edit_message_text(
                text=_format_signals_text(result),
                parse_mode="HTML",
                reply_markup=make_signals_overview_buttons(_get_top_signal(result)),
            )
            return

        if action == "wallet_link":
            result = await execute_tool(
                telegram_user_id=user_id,
                tool_name="start_wallet_link",
                tool_args={},
            )
            if result.get("success"):
                await query.answer("Wallet link ready.")
                await query.edit_message_text(
                    text=(
                        "<b>Link Your Wallet</b>\n\n"
                        "Open the secure link below, connect your wallet, and sign the "
                        "verification nonce. No private keys ever enter Telegram."
                    ),
                    parse_mode="HTML",
                    reply_markup=make_wallet_link_button(result["data"]["link"]),
                )
                return

            await query.answer(result.get("error", "Wallet link unavailable"), show_alert=True)
            return

        if action == "wallet_status":
            result = await execute_tool(
                telegram_user_id=user_id,
                tool_name="check_wallet_link_status",
                tool_args={},
            )
            await query.answer()
            await query.edit_message_text(
                text=_format_wallet_status(result),
                parse_mode="HTML",
                reply_markup=make_wallet_status_buttons(),
            )
            return

        if action == "positions":
            result = await execute_tool(
                telegram_user_id=user_id,
                tool_name="get_position_status",
                tool_args={},
            )
            await query.answer()
            await query.edit_message_text(
                text=_format_positions(result),
                parse_mode="HTML",
                reply_markup=make_positions_buttons(),
            )
            return

        if action == "settings":
            result = await execute_tool(
                telegram_user_id=user_id,
                tool_name="get_user_preferences",
                tool_args={},
            )
            await query.answer()
            await query.edit_message_text(
                text=_format_settings(result),
                parse_mode="HTML",
                reply_markup=make_settings_buttons(),
            )
            return

        await query.answer("Action unavailable.", show_alert=True)

    except Exception as exc:
        log.exception("nav_action_error", error=str(exc))
        await query.answer("Action failed.", show_alert=True)


async def handle_signal_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    try:
        _, params = _parse_callback_data(query.data or "")
        action = params.get("action")

        if action == "explain":
            signal_id = int(params.get("signal_id", 0))
            result = await execute_tool(
                telegram_user_id=user_id,
                tool_name="explain_signal",
                tool_args={"signal_id": signal_id},
            )
            await query.answer()
            await query.edit_message_text(
                text=_format_signal_explanation(result),
                parse_mode="HTML",
                reply_markup=make_signals_overview_buttons(),
            )
            return

        if action == "preview_buy":
            coin_symbol = params.get("coin", "").upper()
            amount_usd = float(min(settings.max_position_size_usd, 25.0))
            result = await execute_tool(
                telegram_user_id=user_id,
                tool_name="preview_trade",
                tool_args={
                    "coin_symbol": coin_symbol,
                    "action": "buy",
                    "amount_usd": amount_usd,
                },
            )
            if result.get("success"):
                await query.answer("Preview ready.")
                await query.edit_message_text(
                    text=(
                        f"<b>Trade Preview</b>\n\n"
                        f"{result['data']['message']}\n\n"
                        "Safety checks stay on until you confirm."
                    ),
                    parse_mode="HTML",
                    reply_markup=make_trade_preview_buttons(
                        coin_symbol,
                        "buy",
                        amount_usd,
                    ),
                )
                return

            await query.answer(result.get("error", "Preview unavailable"), show_alert=True)
            return

        await query.answer("Signal action unavailable.", show_alert=True)

    except Exception as exc:
        log.exception("signal_action_error", error=str(exc))
        await query.answer("Signal action failed.", show_alert=True)


def _get_top_signal(result: dict[str, Any]) -> dict[str, Any] | None:
    signals = result.get("data", {}).get("signals", [])
    if not signals:
        return None
    return signals[0]


def _format_signals_text(result: dict[str, Any]) -> str:
    if not result.get("success"):
        return "<b>Top Signals</b>\n\nI couldn’t load signals right now."

    signals = result.get("data", {}).get("signals", [])
    if not signals:
        return (
            "<b>Top Signals</b>\n\n"
            "No strong Zora signals are live right now.\n\n"
            "Track a creator or refresh again soon."
        )

    lines = ["<b>Top Zora Signals</b>\n"]
    for signal in signals[:3]:
        lines.append(
            f"• <code>#{signal['id']}</code> <b>{signal['coin_symbol']}</b>  "
            f"score <b>{signal['score']}</b>  {signal['recommendation'].replace('_', ' ')}"
        )
    lines.append("\nUse the buttons below to explain the top setup or preview a buy.")
    return "\n".join(lines)


def _format_wallet_status(result: dict[str, Any]) -> str:
    if not result.get("success"):
        return "<b>Wallet</b>\n\nI couldn’t load wallet status right now."

    data = result.get("data", {})
    if data.get("wallet_linked"):
        return (
            "<b>Wallet</b>\n\n"
            "Linked: <b>Yes</b>\n"
            f"Trading enabled: <b>{'Yes' if data.get('trading_enabled') else 'No'}</b>\n\n"
            "You can ask for previews before any real trade action."
        )
    return (
        "<b>Wallet</b>\n\n"
        "No wallet is linked yet.\n\n"
        "Link a wallet to unlock live trade previews and gated execution."
    )


def _format_positions(result: dict[str, Any]) -> str:
    if not result.get("success"):
        return "<b>Positions</b>\n\nI couldn’t load positions right now."

    positions = result.get("data", {}).get("positions", [])
    if not positions:
        return (
            "<b>Positions</b>\n\n"
            "No open positions right now.\n\n"
            "Review top signals or preview a trade to get moving."
        )

    lines = ["<b>Open Positions</b>\n"]
    for position in positions[:3]:
        lines.append(
            f"• <code>#{position['id']}</code> <b>{position['coin']}</b>  "
            f"${position['size_usd']:.0f} @ ${position['entry_price']:.6f}"
        )
    return "\n".join(lines)


def _format_settings(result: dict[str, Any]) -> str:
    prefs = result.get("data", {}).get("preferences", {}) if result.get("success") else {}
    mode = prefs.get("mode", settings.default_creator_mode).replace("_", " ")
    risk = prefs.get("risk", "default")
    return (
        "<b>Settings Snapshot</b>\n\n"
        f"Creator mode: <b>{mode}</b>\n"
        f"Risk profile: <b>{risk}</b>\n"
        f"Wallet linking: <b>{'On' if settings.enable_wallet_linking else 'Off'}</b>\n\n"
        "Use chat to update preferences, for example: <code>set my mode to hybrid</code>."
    )


def _format_signal_explanation(result: dict[str, Any]) -> str:
    if not result.get("success"):
        return "<b>Signal Explanation</b>\n\nI couldn’t explain that signal right now."

    data = result.get("data", {})
    return (
        f"<b>Why {data.get('coin', 'this coin')} was flagged</b>\n\n"
        f"{data.get('explanation', 'No explanation available.')}\n\n"
        f"Recommendation: <b>{str(data.get('recommendation', '')).replace('_', ' ')}</b>"
    )


BUTTON_HANDLERS = {
    TRADE_CONFIRM_PREFIX: handle_trade_confirm,
    TRADE_CANCEL_PREFIX: handle_trade_cancel,
    CLOSE_POSITION_PREFIX: handle_close_position,
    TRACK_CREATOR_PREFIX: handle_track_creator_confirm,
    CREATOR_PREFIX: handle_creator_action,
    NAV_PREFIX: handle_nav_action,
    SIGNAL_PREFIX: handle_signal_action,
}


async def generic_callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query:
        return

    callback_data = query.data or ""
    prefix, _ = _parse_callback_data(callback_data)
    handler = BUTTON_HANDLERS.get(prefix)
    if handler is None:
        log.warning("unknown_callback", callback_data=callback_data)
        await query.answer("Action unavailable.", show_alert=True)
        return

    await handler(update, context)
