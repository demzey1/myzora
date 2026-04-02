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
    row1 = [InlineKeyboardButton("Explain", callback_data=f"explain:{signal_id}")]
    row2 = [InlineKeyboardButton("Ignore", callback_data=f"ignore:{signal_id}")]
    row3 = [InlineKeyboardButton("Refresh", callback_data=f"refresh:{signal_id}")]
    if include_live:
        row1.append(
            InlineKeyboardButton("Review Live", callback_data=f"approve_live:{signal_id}")
        )
    return InlineKeyboardMarkup([row1, row2, row3])


def format_status(
    *,
    paper_trading: bool,
    live_trading: bool,
    open_paper_positions: int,
    open_live_positions: int,
    total_signals_today: int,
    kill_switch_active: bool,
) -> str:
    live_icon = "🟢" if live_trading else "🔴"
    kill_icon = "🛑" if kill_switch_active else "✅"
    sim_text = "available" if paper_trading else "off"

    return (
        "<b>Zora Signal Bot</b>\n"
        "<i>Premium creator-led signal and trading assistant</i>\n\n"
        f"{kill_icon} Safety state: <b>{'Locked' if kill_switch_active else 'Ready'}</b>\n"
        f"{live_icon} Live execution: <b>{'Enabled' if live_trading else 'Guarded'}</b>\n"
        f"Simulation mode: <b>{sim_text}</b>\n\n"
        "<b>Today</b>\n"
        f"Signals detected: <b>{total_signals_today}</b>\n"
        f"Open live positions: <b>{open_live_positions}</b>\n"
        f"Open simulation positions: <b>{open_paper_positions}</b>\n\n"
        "Use the buttons below to move into signals, wallet, positions, or settings."
    )


def format_help() -> str:
    return (
        "<b>Zora Signal Bot</b>\n"
        "<i>Chat-first creator tracking, signal review, and trading guidance</i>\n\n"
        "<b>What I can do</b>\n"
        "• Track creators and watch for Zora-linked setups\n"
        "• Show top signals and explain exactly why they were flagged\n"
        "• Check coin market state and guide secure wallet linking\n"
        "• Preview trades with safety gates before any real action\n\n"
        "<b>Best way to use me</b>\n"
        "Just chat naturally. You do not need commands for normal use.\n\n"
        "Examples:\n"
        "• <code>track @creatorname</code>\n"
        "• <code>show top signals</code>\n"
        "• <code>why was this flagged?</code>\n"
        "• <code>link my wallet</code>\n\n"
        "<b>Advanced commands</b>\n"
        "<i>Secondary admin and fallback controls</i>\n"
        "<code>/signals</code>  <code>/positions</code>  <code>/config</code>  "
        "<code>/features</code>  <code>/health</code>"
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
