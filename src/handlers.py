"""
Telegram command handlers
--------------------------
  /start  — subscribe (shows Buy button if payment required)
  /stop   — unsubscribe
  /status — show subscription status + expiry
  /stats  — admin-only: subscriber counts
"""

import logging
import os
from datetime import timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .db import (
    subscribe, unsubscribe, is_subscribed, is_paid,
    get_expiry, count_active, count_paid, count_total,
)

logger = logging.getLogger("subbot.handlers")

LINE        = "━" * 28
_ADMIN_ID   = int(os.getenv("ADMIN_CHAT_ID", "0"))
FREE_ACCESS = os.getenv("FREE_ACCESS", "false").lower() == "true"

# Label shown on the buy button (set via env or default)
_CHANNEL_NAME = os.getenv("CHANNEL_NAME", "Futures Signals")


def _buy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 Buy Subscription", callback_data="buy")
    ]])


# ------------------------------------------------------------------
# /start
# ------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    chat_id = update.effective_chat.id

    subscribe(chat_id, user.username, user.first_name)

    if FREE_ACCESS:
        count = count_active()
        await update.message.reply_html(
            f"🟢 <b>Subscribed to {_CHANNEL_NAME}!</b>\n"
            f"{LINE}\n"
            f"You'll receive live trading signals here.\n\n"
            f"  /stop   — unsubscribe\n"
            f"  /status — check your subscription\n"
            f"{LINE}\n"
            f"<i>{count} active subscribers</i>",
        )
        logger.info(f"Free subscriber: {user.username or chat_id}  (total active: {count})")
        return

    # Paid mode
    if is_paid(chat_id):
        expiry     = get_expiry(chat_id)
        expiry_str = expiry.astimezone(timezone.utc).strftime("%d %b %Y") if expiry else "—"
        await update.message.reply_html(
            f"✅ <b>You're subscribed to {_CHANNEL_NAME}!</b>\n"
            f"{LINE}\n"
            f"Subscription active until: <b>{expiry_str}</b>\n\n"
            f"  /status — check details\n"
            f"  /buy    — extend subscription\n"
            f"  /stop   — unsubscribe",
        )
    else:
        await update.message.reply_html(
            f"👋 <b>Welcome to {_CHANNEL_NAME}!</b>\n"
            f"{LINE}\n"
            f"Get live crypto trading signals directly in this chat.\n\n"
            f"• All confirmed signals with full TP/SL levels\n"
            f"• Multiple strategies — EMA, S/R, Structure Break and more\n"
            f"• Paid via Telegram Stars — no external accounts needed\n"
            f"{LINE}\n"
            f"Tap the button below to subscribe 👇",
            reply_markup=_buy_keyboard(),
        )
    logger.info(f"Start: {user.username or chat_id}  paid={is_paid(chat_id)}")


# ------------------------------------------------------------------
# /stop
# ------------------------------------------------------------------

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not is_subscribed(chat_id):
        await update.message.reply_html(
            "You're not currently subscribed.\nUse /start to subscribe."
        )
        return

    unsubscribe(chat_id)
    await update.message.reply_html(
        "🔴 <b>Unsubscribed.</b>\n"
        "You won't receive any more signals.\n"
        "Use /start to resubscribe anytime."
    )
    logger.info(f"Unsubscribed: {update.effective_user.username or chat_id}")


# ------------------------------------------------------------------
# /status
# ------------------------------------------------------------------

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if FREE_ACCESS:
        active = is_subscribed(chat_id)
        state  = "🟢 <b>Active</b>" if active else "🔴 <b>Inactive</b> — use /start"
        await update.message.reply_html(f"Subscription: {state}")
        return

    paid   = is_paid(chat_id)
    expiry = get_expiry(chat_id)

    if paid and expiry:
        expiry_str = expiry.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")
        await update.message.reply_html(
            f"✅ <b>Subscription Active</b>\n"
            f"{LINE}\n"
            f"Expires: <code>{expiry_str}</code>\n"
            f"Use /buy to extend."
        )
    else:
        await update.message.reply_html(
            "🔴 <b>No active subscription.</b>\n"
            "Use /buy to subscribe.",
            reply_markup=_buy_keyboard(),
        )


# ------------------------------------------------------------------
# /stats  (admin only)
# ------------------------------------------------------------------

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if _ADMIN_ID and chat_id != _ADMIN_ID:
        await update.message.reply_html("⛔ Admin only.")
        return

    active = count_active()
    paid   = count_paid()
    total  = count_total()

    await update.message.reply_html(
        f"📊 <b>Subscriber Stats</b>\n"
        f"{LINE}\n"
        f"Paid active : <code>{paid}</code>\n"
        f"All active  : <code>{active}</code>\n"
        f"Total ever  : <code>{total}</code>\n"
        f"Inactive    : <code>{total - active}</code>"
    )
