"""
Channel access management
--------------------------
Trial flow:
  • First join request → approved instantly with 7-day free trial
  • After 7 days → user is kicked + DM'd to subscribe
  • Subsequent join request (trial used, not paid) → payment menu shown

Payment flow (after Stars / crypto / manual approval):
  • Has pending join request → approve it
  • No pending join request  → send a one-time invite link (24h, 1 use)

Environment variables:
  CHANNEL_ID        Telegram channel ID, e.g. -1001234567890 (required)
  TRIAL_DAYS        Free trial length in days (default: 7)
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from telegram import Bot, Update
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from .db import (
    subscribe,
    set_join_request, has_join_request,
    has_used_trial, set_trial, set_trial_kicked,
    get_expired_trial_users,
)

logger     = logging.getLogger("subbot.channel")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "7"))
LINE       = "━" * 28


# ------------------------------------------------------------------
# Incoming join request from the channel
# ------------------------------------------------------------------

async def handle_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not CHANNEL_ID:
        return

    request = update.chat_join_request
    user    = request.from_user
    chat_id = user.id

    subscribe(chat_id, user.username, user.first_name)

    # ── First-time user: give free trial ──────────────────────────
    if not has_used_trial(chat_id):
        try:
            await ctx.bot.approve_chat_join_request(
                chat_id=CHANNEL_ID,
                user_id=chat_id,
            )
        except TelegramError as e:
            logger.warning(f"Could not approve trial join for {chat_id}: {e}")
            return

        expiry     = set_trial(chat_id, TRIAL_DAYS)
        expiry_str = expiry.strftime("%d %b %Y")

        try:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"👋 <b>Welcome! You have a {TRIAL_DAYS}-day free trial.</b>\n"
                    f"{LINE}\n"
                    f"Your trial expires on <b>{expiry_str}</b>.\n\n"
                    f"After that you'll need an active subscription to stay in the channel.\n"
                    f"Use /buy anytime to subscribe before your trial ends."
                ),
                parse_mode="HTML",
            )
        except Forbidden:
            pass  # They didn't start the bot — they're still in the channel, just no DM

        logger.info(f"Trial started for {user.username or chat_id} — expires {expiry_str}")
        return

    # ── Trial already used: require payment ───────────────────────
    set_join_request(chat_id, True)

    try:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=(
                f"👋 <b>Welcome back!</b>\n"
                f"{LINE}\n"
                f"Your free trial has been used.\n"
                f"You need an active subscription to join the channel.\n\n"
                f"Use /buy to subscribe and get instant access."
            ),
            parse_mode="HTML",
        )
    except Forbidden:
        # Can't DM — decline the request so they're not stuck in limbo
        try:
            await ctx.bot.decline_chat_join_request(
                chat_id=CHANNEL_ID,
                user_id=chat_id,
            )
        except TelegramError:
            pass
        set_join_request(chat_id, False)
        logger.info(f"Could not DM {chat_id} — join request declined")
        return

    logger.info(f"Returning user {user.username or chat_id} prompted to subscribe")


# ------------------------------------------------------------------
# Kick users whose free trial has expired
# ------------------------------------------------------------------

async def kick_expired_trials(bot: Bot):
    """Called hourly. Kicks and notifies users whose trial has expired."""
    if not CHANNEL_ID:
        return

    expired = get_expired_trial_users()
    if not expired:
        return

    logger.info(f"Kicking {len(expired)} expired trial user(s)")

    for chat_id in expired:
        try:
            # ban then immediately unban = kick without permanent block
            await bot.ban_chat_member(chat_id=CHANNEL_ID, user_id=chat_id)
            await bot.unban_chat_member(chat_id=CHANNEL_ID, user_id=chat_id)
            set_trial_kicked(chat_id)
            logger.info(f"Kicked expired trial: {chat_id}")
        except TelegramError as e:
            logger.warning(f"Could not kick {chat_id}: {e}")
            # Mark as kicked anyway to avoid retrying every hour
            set_trial_kicked(chat_id)

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⏰ <b>Your free trial has ended.</b>\n"
                    f"{LINE}\n"
                    f"You've been removed from the channel.\n\n"
                    f"Subscribe now to get back in:\n"
                    f"/buy"
                ),
                parse_mode="HTML",
            )
        except Forbidden:
            pass  # They blocked the bot — nothing we can do


# ------------------------------------------------------------------
# Grant access after payment
# ------------------------------------------------------------------

async def grant_access(bot: Bot, chat_id: int):
    """
    Called after a subscription is activated (any payment method).
    Approves the pending join request OR sends a one-time invite link.
    """
    if not CHANNEL_ID:
        return

    if has_join_request(chat_id):
        try:
            await bot.approve_chat_join_request(
                chat_id=CHANNEL_ID,
                user_id=chat_id,
            )
            set_join_request(chat_id, False)
            logger.info(f"Approved join request for {chat_id}")

            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ <b>You're in!</b>\n"
                    f"{LINE}\n"
                    f"Your join request has been approved.\n"
                    f"Open the channel to start receiving signals."
                ),
                parse_mode="HTML",
            )
        except BadRequest as e:
            # Join request expired — fall back to invite link
            logger.warning(f"approve_chat_join_request failed for {chat_id}: {e} — sending invite link")
            set_join_request(chat_id, False)
            await _send_invite_link(bot, chat_id)
        except TelegramError as e:
            logger.warning(f"Could not approve join request for {chat_id}: {e}")
    else:
        await _send_invite_link(bot, chat_id)


async def _send_invite_link(bot: Bot, chat_id: int):
    """Create a single-use invite link and send it to the user."""
    try:
        expiry = datetime.now(timezone.utc) + timedelta(hours=24)
        link   = await bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            expire_date=expiry,
            member_limit=1,
            name=f"sub_{chat_id}",
        )
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ <b>Subscription Activated!</b>\n"
                f"{LINE}\n"
                f"Here's your invite link — valid for <b>24 hours</b>, one use only:\n\n"
                f"{link.invite_link}\n\n"
                f"<i>Tap it to rejoin the channel now.</i>"
            ),
            parse_mode="HTML",
        )
        logger.info(f"Invite link sent to {chat_id}")
    except TelegramError as e:
        logger.warning(f"Could not create invite link for {chat_id}: {e}")
