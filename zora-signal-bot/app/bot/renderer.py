"""
app/bot/renderer.py
─────────────────────────────────────────────────────────────────────────────
Pure functions that turn domain objects into formatted Telegram message strings.
All formatting logic lives here — handlers stay clean.
Uses MarkdownV2 where appropriate (escape special chars carefully).
"""

from __future__ import annotations

from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import Recommendation, Signal


def _age_str(dt: datetime | None) -> str:
    if dt is None:
        return "unknown"
    delta = datetime.now(timezone.utc) - dt
    minutes = int(delta.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


RECOMMENDATION_EMOJI = {
    Recommendation.IGNORE: "🔇",
    Recommendation.WATCH: "👀",
    Recommendation.ALERT: "🚨",
    Recommendation.PAPER_TRADE: "📝",
    Recommendation.LIVE_TRADE_READY: "⚡",
}


def format_signal_alert(
    *,
    signal: Signal,
    x_username: str,
    follower_count: int | None,
    post_text: str,
    post_age_dt: datetime | None,
    engagement_velocity: str,
    coin_symbol: str,
    coin_age_dt: datetime | None,
    price_usd: float | None,
    liquidity_usd: float | None,
    slippage_bps: int | None,
    volume_5m_usd: float | None,
) -> str:
    """Render a full signal alert message in Telegram HTML format."""
    rec = signal.recommendation
    emoji = RECOMMENDATION_EMOJI.get(rec, "ℹ️")

    followers_str = f"{follower_count:,}" if follower_count else "N/A"
    price_str = f"${price_usd:.6f}" if price_usd else "N/A"
    liq_str = f"${liquidity_usd:,.0f}" if liquidity_usd else "N/A"
    slip_str = f"{slippage_bps / 100:.2f}%" if slippage_bps is not None else "N/A"
    vol_str = f"${volume_5m_usd:,.0f}" if volume_5m_usd else "N/A"

    # Truncate post text
    snippet = (post_text[:200] + "…") if len(post_text) > 200 else post_text

    lines = [
        f"{emoji} <b>ZORA SIGNAL — {rec.value}</b>",
        "",
        f"<b>Account:</b> @{x_username}",
        f"<b>Followers:</b> {followers_str}",
        f"<b>Post age:</b> {_age_str(post_age_dt)}",
        f"<b>Engagement velocity:</b> {engagement_velocity}",
        f"<b>Post:</b> <i>{snippet}</i>",
        "",
        "📊 <b>Coin</b>",
        f"  Symbol: <code>{coin_symbol}</code>",
        f"  Coin age: {_age_str(coin_age_dt)}",
        f"  Price: {price_str}",
        f"  Liquidity: {liq_str}",
        f"  Slippage @ target: {slip_str}",
        f"  Volume 5m: {vol_str}",
        "",
        "🎯 <b>Score</b>",
        f"  Deterministic: <b>{signal.deterministic_score:.0f}</b>",
    ]

    if signal.llm_score is not None:
        lines.append(f"  LLM classification: <b>{signal.llm_score:.0f}</b>")

    lines += [
        f"  Final: <b>{signal.final_score:.0f}</b>",
        "",
        f"<b>Decision:</b> {rec.value}",
    ]

    if signal.risk_notes:
        lines += ["", f"⚠️ <b>Risk notes:</b> {signal.risk_notes}"]

    lines += ["", f"<code>Signal ID: {signal.id}</code>"]

    return "\n".join(lines)


def signal_inline_keyboard(signal_id: int, include_live: bool = False) -> InlineKeyboardMarkup:
    """Build the inline action buttons shown beneath a signal alert."""
    row1 = [
        InlineKeyboardButton("✅ Paper trade", callback_data=f"approve_paper:{signal_id}"),
        InlineKeyboardButton("🙈 Ignore", callback_data=f"ignore:{signal_id}"),
    ]
    row2 = [
        InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{signal_id}"),
    ]
    if include_live:
        row2.append(
            InlineKeyboardButton("⚡ Approve LIVE", callback_data=f"approve_live:{signal_id}")
        )
    return InlineKeyboardMarkup([row1, row2])


def format_status(
    *,
    paper_trading: bool,
    live_trading: bool,
    open_paper_positions: int,
    open_live_positions: int,
    total_signals_today: int,
    kill_switch_active: bool,
) -> str:
    paper_icon = "🟢" if paper_trading else "🔴"
    live_icon = "🟢" if live_trading else "🔴"
    kill_icon = "🛑" if kill_switch_active else "✅"

    return (
        "📡 <b>Zora Signal Bot — Status</b>\n\n"
        f"{kill_icon} Kill switch: {'ACTIVE' if kill_switch_active else 'inactive'}\n"
        f"{paper_icon} Paper trading: {'ON' if paper_trading else 'OFF'}\n"
        f"{live_icon} Live trading:  {'ON' if live_trading else 'OFF'}\n\n"
        f"Open paper positions: <b>{open_paper_positions}</b>\n"
        f"Open live positions:  <b>{open_live_positions}</b>\n"
        f"Signals today:        <b>{total_signals_today}</b>"
    )


def format_help() -> str:
    return (
        "🤖 <b>Zora Signal Bot — Commands</b>\n\n"
        "<b>Info</b>\n"
        "/status  — system status\n"
        "/health  — service health check\n"
        "/signals — recent signals\n"
        "/recent  — recent posts ingested\n"
        "/positions — open positions\n"
        "/pnl     — paper trading P&amp;L summary\n\n"
        "<b>Watchlist</b>\n"
        "/watchlist              — list monitored accounts\n"
        "/addaccount @handle     — add X account\n"
        "/removeaccount @handle  — remove X account\n\n"
        "<b>Scoring</b>\n"
        "/score &lt;url_or_id&gt;  — score a specific post\n\n"
        "<b>Trading</b>\n"
        "/paper_on   — enable paper trading\n"
        "/paper_off  — disable paper trading\n"
        "/live_on    — enable live trading (⚠️ admin)\n"
        "/live_off   — disable live trading\n\n"
        "<b>Approval</b>\n"
        "/approve &lt;signal_id&gt; — approve a signal\n"
        "/reject  &lt;signal_id&gt; — reject a signal\n\n"
        "<b>Admin</b>\n"
        "/config    — show current configuration\n"
        "/kill      — 🛑 emergency kill switch\n\n"
        "<b>Creator Overrides</b>\n"
        "/blacklist @handle  — blacklist account or coin\n"
        "/whitelist @handle  — whitelist with score boost\n"
        "/overrides          — list all active overrides\n"
    )


# ══════════════════════════════════════════════════════════════════════════════
# CREATOR INTENT SIGNAL ALERT
# ══════════════════════════════════════════════════════════════════════════════

def format_creator_signal_alert(
    *,
    creator,
    post,
    classification,
    candidates: list,
) -> str:
    """Render a creator intent signal alert message in Telegram HTML."""
    import json as _json

    sentiment_emoji = {
        "bullish": "🚀",
        "bearish": "🐻",
        "neutral": "😐",
        "noise": "🔇",
    }.get(classification.sentiment.value, "ℹ️")

    fol = f"{creator.follower_count:,}" if creator.follower_count else "?"

    keywords = []
    if classification.keywords_json:
        try:
            keywords = _json.loads(classification.keywords_json)
        except Exception:
            pass
    narratives = []
    if classification.narratives_json:
        try:
            narratives = _json.loads(classification.narratives_json)
        except Exception:
            pass

    post_snippet = (post.text or "")[:200].replace("<", "&lt;").replace(">", "&gt;")
    if len(post.text or "") > 200:
        post_snippet += "…"

    lines = [
        f"{sentiment_emoji} <b>BULLISH CREATOR SIGNAL</b>",
        "",
        f"Creator:    <b>@{creator.x_username}</b>",
        f"Followers:  {fol}",
        f"Post age:   {_age_str(post.posted_at)}",
        f"Sentiment:  <b>{classification.sentiment.value.title()}</b>",
        f"Conviction: <b>{classification.conviction_score}/100</b>",
    ]
    if keywords:
        lines.append(f"Keywords:   {', '.join(keywords[:6])}")
    if narratives:
        lines.append(f"Narrative:  {', '.join(narratives[:3])}")

    lines += ["", f"<i>{post_snippet}</i>", "", "📊 <b>Top Coin Candidates</b>"]

    match_labels = {
        "creator_coin":  "creator coin",
        "content_coin":  "content coin",
        "keyword_match": "keyword match",
        "trending_match":"trending match",
    }

    for i, c in enumerate(candidates, 1):
        symbol = c.symbol or "???"
        match = match_labels.get(c.match_type, c.match_type)
        liq = f"${c.liquidity_usd:,.0f}" if c.liquidity_usd else "N/A"
        slip = f"{c.slippage_bps/100:.1f}%" if c.slippage_bps else "N/A"
        vol = f"${c.volume_5m_usd:,.0f}" if c.volume_5m_usd else "N/A"
        flags = c.risk_flags.replace("|", " · ") if c.risk_flags else "none"

        lines += [
            "",
            f"<b>{i}. {symbol}</b>  [{match}]",
            f"   Relevance:  {c.final_rank_score}/100",
            f"   Liquidity:  {liq}",
            f"   Slippage:   {slip}",
            f"   Vol 5m:     {vol}",
            f"   Risk:       {flags}",
        ]

    # Recommendation based on top candidate
    rec = "WATCH"
    if candidates and candidates[0].final_rank_score >= 70:
        rec = "PAPER_TRADE"
    elif candidates and candidates[0].final_rank_score >= 55:
        rec = "ALERT"

    lines += [
        "",
        f"📌 <b>Decision: {rec}</b>",
        f"<code>Post ID: {post.id}</code>",
    ]
    return "\n".join(lines)


def creator_signal_keyboard(creator_post_id: int, coin_address: str | None = None):
    """Inline keyboard for creator signal alerts."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    # Build Zora buy URL if we have the coin address
    zora_url = (
        f"https://zora.co/collect/base:{coin_address}"
        if coin_address else "https://zora.co/explore"
    )

    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👀 Watch",      callback_data=f"cs_watch:{creator_post_id}"),
        InlineKeyboardButton("🛒 Buy on Zora", url=zora_url),
        InlineKeyboardButton("🙈 Ignore",     callback_data=f"cs_ignore:{creator_post_id}"),
    ], [
        InlineKeyboardButton("🔄 Refresh",    callback_data=f"cs_refresh:{creator_post_id}"),
        InlineKeyboardButton("🤖 Ask AI",     callback_data=f"cs_ai:{creator_post_id}"),
    ]])
