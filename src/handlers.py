"""
Telegram command handlers
--------------------------
  /start  — first time: start 7-day trial + send channel invite link
            trial active: welcome back + channel link button
            trial expired / not paid: payment menu
            paid: status + channel link button
  /stop   — unsubscribe
  /status — show subscription / trial status
  /stats  — admin-only: subscriber counts
"""

import logging
import os
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .db import (
    subscribe, unsubscribe, is_subscribed,
    is_paid, get_expiry,
    has_used_trial, set_trial, is_trial_active, get_trial_expiry,
    count_active, count_paid, count_total,
)
from .channel import grant_access, send_invite_link

logger       = logging.getLogger("subbot.handlers")
LINE         = "━" * 28
DLINE        = "─" * 28
_ADMIN_ID    = int(os.getenv("ADMIN_CHAT_ID", "0"))
FREE_ACCESS  = os.getenv("FREE_ACCESS", "false").lower() == "true"
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "Futures Signals")
TRIAL_DAYS   = int(os.getenv("TRIAL_DAYS", "7"))


# ------------------------------------------------------------------
# Keyboards
# ------------------------------------------------------------------

def _main_keyboard() -> InlineKeyboardMarkup:
    """For users with active access (trial or paid)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📡 Get Channel Link", callback_data="get_access")],
        [
            InlineKeyboardButton("💳 Subscribe",  callback_data="buy"),
            InlineKeyboardButton("📊 My Status",  callback_data="my_status"),
        ],
    ])


def _buy_keyboard() -> InlineKeyboardMarkup:
    """For users with no access."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Subscribe Now",   callback_data="buy")],
        [InlineKeyboardButton("📊 My Status",       callback_data="my_status")],
    ])


# ------------------------------------------------------------------
# /start
# ------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    chat_id = update.effective_chat.id

    subscribe(chat_id, user.username, user.first_name)

    # ── Free access mode ──────────────────────────────────────────
    if FREE_ACCESS:
        await update.message.reply_html(
            f"🟢 <b>Welcome to {CHANNEL_NAME}!</b>\n"
            f"{LINE}\n"
            f"You have free access.\n"
            f"Tap below to join the channel.",
            reply_markup=_main_keyboard(),
        )
        return

    # ── Active paid subscription ──────────────────────────────────
    if is_paid(chat_id):
        expiry     = get_expiry(chat_id)
        expiry_str = expiry.strftime("%d %b %Y") if expiry else "—"
        await update.message.reply_html(
            f"✅ <b>Welcome back!</b>\n"
            f"{LINE}\n"
            f"Subscription active until <b>{expiry_str}</b>.\n\n"
            f"Tap <b>Get Channel Link</b> to join.",
            reply_markup=_main_keyboard(),
        )
        return

    # ── Active free trial ─────────────────────────────────────────
    if is_trial_active(chat_id):
        expiry     = get_trial_expiry(chat_id)
        expiry_str = expiry.strftime("%d %b %Y") if expiry else "—"
        await update.message.reply_html(
            f"👋 <b>Welcome back!</b>\n"
            f"{LINE}\n"
            f"Your free trial is active until <b>{expiry_str}</b>.\n\n"
            f"Tap <b>Get Channel Link</b> to join.\n"
            f"Subscribe before your trial ends to keep access.",
            reply_markup=_main_keyboard(),
        )
        return

    # ── First time ever: give free trial ─────────────────────────
    if not has_used_trial(chat_id):
        expiry     = set_trial(chat_id, TRIAL_DAYS)
        expiry_str = expiry.strftime("%d %b %Y")
        await update.message.reply_html(
            f"👋 <b>Welcome to {CHANNEL_NAME}!</b>\n"
            f"{LINE}\n"
            f"🎁 You have a <b>{TRIAL_DAYS}-day free trial!</b>\n"
            f"Trial expires: <b>{expiry_str}</b>\n"
            f"{DLINE}\n"
            f"Tap <b>Get Channel Link</b> below to join now.\n"
            f"Subscribe before your trial ends to keep access.",
            reply_markup=_main_keyboard(),
        )
        logger.info(f"Trial started: {user.username or chat_id} — expires {expiry_str}")
        return

    # ── Trial expired, not paid ───────────────────────────────────
    await update.message.reply_html(
        f"👋 <b>Welcome back to {CHANNEL_NAME}!</b>\n"
        f"{LINE}\n"
        f"⏰ Your free trial has ended.\n\n"
        f"Subscribe to get back into the channel.",
        reply_markup=_buy_keyboard(),
    )
    logger.info(f"Trial expired, prompted to subscribe: {user.username or chat_id}")


# ------------------------------------------------------------------
# Callback: 📡 Get Channel Link
# ------------------------------------------------------------------

async def cb_get_access(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()

    if is_paid(chat_id) or is_trial_active(chat_id) or FREE_ACCESS:
        await send_invite_link(ctx.bot, chat_id)
    else:
        await query.answer(
            "Your trial has expired. Please subscribe first.", show_alert=True
        )


# ------------------------------------------------------------------
# Callback: 📊 My Status
# ------------------------------------------------------------------

async def cb_my_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()

    now = datetime.now(timezone.utc)

    if is_paid(chat_id):
        expiry     = get_expiry(chat_id)
        expiry_str = expiry.strftime("%d %b %Y") if expiry else "—"
        text       = (
            f"📊 <b>Your Status</b>\n"
            f"{LINE}\n"
            f"✅ Subscription active\n"
            f"Expires: <b>{expiry_str}</b>"
        )
        keyboard = _main_keyboard()
    elif is_trial_active(chat_id):
        expiry     = get_trial_expiry(chat_id)
        expiry_str = expiry.strftime("%d %b %Y") if expiry else "—"
        days_left  = (expiry - now).days if expiry else 0
        text       = (
            f"📊 <b>Your Status</b>\n"
            f"{LINE}\n"
            f"🕐 Free trial active — <b>{days_left} day(s) left</b>\n"
            f"Expires: <b>{expiry_str}</b>\n\n"
            f"Subscribe before it ends to keep access."
        )
        keyboard = _main_keyboard()
    elif has_used_trial(chat_id):
        expiry     = get_trial_expiry(chat_id)
        expiry_str = expiry.strftime("%d %b %Y") if expiry else "—"
        text       = (
            f"📊 <b>Your Status</b>\n"
            f"{LINE}\n"
            f"⏰ Trial expired on <b>{expiry_str}</b>\n\n"
            f"Subscribe to regain access."
        )
        keyboard = _buy_keyboard()
    else:
        text     = (
            f"📊 <b>Your Status</b>\n"
            f"{LINE}\n"
            f"❌ No active subscription."
        )
        keyboard = _buy_keyboard()

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


# ------------------------------------------------------------------
# /stop
# ------------------------------------------------------------------

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not is_subscribed(chat_id):
        await update.message.reply_html(
            "You're not currently subscribed.\nUse /start to begin."
        )
        return

    unsubscribe(chat_id)
    await update.message.reply_html(
        "🔴 <b>Unsubscribed.</b>\nUse /start to resubscribe anytime."
    )
    logger.info(f"Unsubscribed: {update.effective_user.username or chat_id}")


# ------------------------------------------------------------------
# /status
# ------------------------------------------------------------------

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now     = datetime.now(timezone.utc)

    if is_paid(chat_id):
        expiry     = get_expiry(chat_id)
        expiry_str = expiry.strftime("%d %b %Y %H:%M UTC") if expiry else "—"
        await update.message.reply_html(
            f"✅ <b>Subscription Active</b>\n"
            f"{LINE}\n"
            f"Expires: <code>{expiry_str}</code>",
            reply_markup=_main_keyboard(),
        )
    elif is_trial_active(chat_id):
        expiry     = get_trial_expiry(chat_id)
        expiry_str = expiry.strftime("%d %b %Y") if expiry else "—"
        days_left  = (expiry - now).days if expiry else 0
        await update.message.reply_html(
            f"🕐 <b>Free Trial Active</b>\n"
            f"{LINE}\n"
            f"Expires: <code>{expiry_str}</code> ({days_left} day(s) left)\n\n"
            f"Subscribe before it ends to keep access.",
            reply_markup=_main_keyboard(),
        )
    elif has_used_trial(chat_id):
        expiry     = get_trial_expiry(chat_id)
        expiry_str = expiry.strftime("%d %b %Y") if expiry else "—"
        await update.message.reply_html(
            f"⏰ <b>Trial Expired</b> — {expiry_str}\n\n"
            f"Use /buy to subscribe.",
            reply_markup=_buy_keyboard(),
        )
    else:
        await update.message.reply_html(
            "❌ <b>No active subscription.</b>\nUse /buy to subscribe.",
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
