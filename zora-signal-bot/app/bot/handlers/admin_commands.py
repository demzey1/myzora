"""
app/bot/handlers/admin_commands.py
─────────────────────────────────────────────────────────────────────────────
Admin feature flag commands.

  /features              — list all features and their current state
  /featureon  <name>     — enable a feature
  /featureoff <name>     — disable a feature
  /restart               — graceful reminder (can't restart from Telegram,
                           but tells admin the docker command)
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.logging_config import get_logger

log = get_logger(__name__)


async def _reply(update: Update, text: str) -> None:
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_features(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/features — show all feature flags and their current state."""
    from app.bot.middleware import check_admin
    if not await check_admin(update, context):
        return

    from app.services.feature_flags import get_all_flags, get_flag_description

    flags = get_all_flags()
    lines = ["⚙️ <b>Feature Flags</b>\n"]
    for flag, enabled in sorted(flags.items()):
        icon = "🟢" if enabled else "🔴"
        desc = get_flag_description(flag)
        lines.append(f"{icon} <code>{flag}</code> — {desc}")

    lines += [
        "",
        "Use /featureon &lt;name&gt; or /featureoff &lt;name&gt; to toggle.",
    ]
    await _reply(update, "\n".join(lines))


async def cmd_featureon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/featureon <name> — enable a feature."""
    from app.bot.middleware import check_admin
    if not await check_admin(update, context):
        return

    if not context.args:
        await _reply(update, "Usage: /featureon &lt;feature_name&gt;\nSee /features for list.")
        return

    from app.services.feature_flags import set_flag
    flag = context.args[0].lower()
    ok, msg = set_flag(flag, True, update.effective_user.id)
    icon = "✅" if ok else "❌"
    await _reply(update, f"{icon} {msg}")


async def cmd_featureoff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/featureoff <name> — disable a feature."""
    from app.bot.middleware import check_admin
    if not await check_admin(update, context):
        return

    if not context.args:
        await _reply(update, "Usage: /featureoff &lt;feature_name&gt;\nSee /features for list.")
        return

    from app.services.feature_flags import set_flag
    flag = context.args[0].lower()

    # Safety: don't allow disabling alerts silently
    if flag == "alerts":
        await _reply(
            update,
            "⚠️ Disabling <b>alerts</b> will stop ALL signal notifications.\n"
            "Are you sure? Send /featureoff alerts confirm"
        )
        if len(context.args) < 2 or context.args[1] != "confirm":
            return

    ok, msg = set_flag(flag, False, update.effective_user.id)
    icon = "✅" if ok else "❌"
    await _reply(update, f"{icon} {msg}")


async def cmd_botstatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/botstatus — full system status including feature flags."""
    from app.bot.middleware import check_admin
    if not await check_admin(update, context):
        return

    from app.services.feature_flags import get_all_flags

    flags = get_all_flags()
    on  = [f for f, v in flags.items() if v]
    off = [f for f, v in flags.items() if not v]

    lines = ["🤖 <b>Bot Status</b>\n"]

    # Feature summary
    if off:
        lines.append(f"🔴 <b>Disabled features ({len(off)}):</b>")
        for f in off:
            lines.append(f"   • {f}")
        lines.append("")

    lines.append(f"🟢 Active features: {len(on)}/{len(flags)}")
    lines += [
        "",
        "Commands:",
        "/features — detailed flag list",
        "/health — dependency health check",
        "/config — scoring + trading config",
        "/creators — watched creator list",
        "/mystatus — your subscription status",
    ]

    await _reply(update, "\n".join(lines))
