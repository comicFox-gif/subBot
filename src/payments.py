"""
Telegram Stars payment handlers
---------------------------------
Flow:
  1. User taps "💳 Buy Subscription" button or sends /buy
  2. Bot sends a Stars invoice
  3. Telegram sends a pre_checkout_query → bot must answer within 10s
  4. User confirms → successful_payment arrives → bot activates subscription

Environment variables:
  SUB_PRICE_STARS    Stars to charge per period  (default: 100)
  SUB_DURATION_DAYS  Days the subscription lasts  (default: 30)

Telegram Stars reference rate: 50 Stars ≈ $1 USD (varies).
"""

import logging
import os
from datetime import timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Update,
)
from telegram.ext import ContextTypes

from .db import subscribe, set_paid, is_paid, get_expiry

logger = logging.getLogger("subbot.payments")

PRICE_STARS    = int(os.getenv("SUB_PRICE_STARS",    "100"))
DURATION_DAYS  = int(os.getenv("SUB_DURATION_DAYS",  "30"))
LINE           = "━" * 28


# ------------------------------------------------------------------
# /buy  — send the Stars invoice
# ------------------------------------------------------------------

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user    = update.effective_user

    # If coming from a callback query, answer it first
    if update.callback_query:
        await update.callback_query.answer()

    # Register the user so they exist in the DB even before paying
    subscribe(chat_id, user.username, user.first_name)

    already_paid = is_paid(chat_id)
    action_word  = "Extend" if already_paid else "Buy"

    await ctx.bot.send_invoice(
        chat_id=chat_id,
        title=f"📡 {DURATION_DAYS}-Day Signal Subscription",
        description=(
            f"Get {DURATION_DAYS} days of live crypto trading signals "
            f"delivered directly to this chat.\n\n"
            f"• All confirmed signals (EMA, S/R, Structure Break, …)\n"
            f"• Real-time TP / SL levels\n"
            f"• Risk:Reward pre-calculated"
        ),
        payload=f"sub_{DURATION_DAYS}d",
        provider_token="",          # Empty string = Telegram Stars (XTR)
        currency="XTR",
        prices=[LabeledPrice(f"{action_word} {DURATION_DAYS} days", PRICE_STARS)],
        protect_content=False,
    )
    logger.info(f"Invoice sent to {user.username or chat_id} ({PRICE_STARS} Stars)")


# ------------------------------------------------------------------
# Callback button handler — "💳 Buy Subscription" from /start
# ------------------------------------------------------------------

async def cb_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles the inline keyboard 'Buy Subscription' button tap."""
    await cmd_buy(update, ctx)


# ------------------------------------------------------------------
# Pre-checkout query — must be answered within 10 seconds
# ------------------------------------------------------------------

async def pre_checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload.startswith("sub_"):
        await query.answer(ok=True)
        logger.info(f"Pre-checkout approved for {query.from_user.id}")
    else:
        await query.answer(ok=False, error_message="Unknown invoice payload.")
        logger.warning(f"Unknown payload: {query.invoice_payload}")


# ------------------------------------------------------------------
# Successful payment — activate the subscription
# ------------------------------------------------------------------

async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    chat_id = update.effective_chat.id
    payment = update.message.successful_payment

    stars   = payment.total_amount          # in Stars (XTR has no decimals)
    payload = payment.invoice_payload

    # Parse duration from payload (e.g. "sub_30d" → 30)
    try:
        days = int(payload.split("_")[1].rstrip("d"))
    except (IndexError, ValueError):
        days = DURATION_DAYS

    # Activate / extend subscription
    subscribe(chat_id, user.username, user.first_name)
    expiry = set_paid(chat_id, days)
    expiry_str = expiry.astimezone(timezone.utc).strftime("%d %b %Y")

    logger.info(
        f"Payment received from {user.username or chat_id}: "
        f"{stars} Stars — sub extended to {expiry_str}"
    )

    await update.message.reply_html(
        f"🎉 <b>Subscription Activated!</b>\n"
        f"{LINE}\n"
        f"✅ {stars} Stars received\n"
        f"📅 Active until: <b>{expiry_str}</b>\n"
        f"{LINE}\n"
        f"You'll now receive all live trading signals here.\n"
        f"Use /status to check your subscription anytime."
    )
