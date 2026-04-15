"""
Channel access management
--------------------------
Handles two scenarios after a user pays:

  A) User sent a channel join request first
     → bot calls approve_chat_join_request
     → user lands in the channel instantly

  B) User paid via the bot directly (no prior join request)
     → bot creates a single-use invite link (expires 24h, 1 use)
     → bot sends the link to the user

Also handles incoming ChatJoinRequest events:
  → bot tries to DM the user with the payment menu
  → if the bot can't DM (user never started it), decline the request
    with a note to start the bot first

Environment variables:
  CHANNEL_ID    Telegram channel ID, e.g. -1001234567890 (required for gating)
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from telegram import Bot
from telegram.error import Forbidden, TelegramError, BadRequest
from telegram import Update
from telegram.ext import ContextTypes

from .db import subscribe, set_join_request, has_join_request

logger     = logging.getLogger("subbot.channel")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
LINE       = "━" * 28


# ------------------------------------------------------------------
# Incoming join request from the channel
# ------------------------------------------------------------------

async def handle_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Fired when someone taps "Request to Join" on the channel.
    Bot tries to DM them with the payment menu.
    """
    if not CHANNEL_ID:
        return

    request = update.chat_join_request
    user    = request.from_user
    chat_id = user.id

    # Register in DB and flag the pending join request
    subscribe(chat_id, user.username, user.first_name)
    set_join_request(chat_id, True)

    try:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=(
                f"👋 <b>Hey {user.first_name}!</b>\n"
                f"{LINE}\n"
                f"You requested to join the channel.\n"
                f"To get access, you need an active subscription.\n\n"
                f"Use /buy to choose a payment method and get instant access."
            ),
            parse_mode="HTML",
        )
        logger.info(f"Join request DM sent to {user.username or chat_id}")
    except Forbidden:
        # User never started the bot — can't DM them
        # Decline the request with a note
        try:
            await ctx.bot.decline_chat_join_request(
                chat_id=CHANNEL_ID,
                user_id=chat_id,
            )
        except TelegramError as e:
            logger.warning(f"Could not decline join request for {chat_id}: {e}")

        set_join_request(chat_id, False)
        logger.info(f"Could not DM {chat_id} — join request declined")
    except TelegramError as e:
        logger.warning(f"Error handling join request for {chat_id}: {e}")


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
        # Path A: approve the pending join request
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
            # Join request may have expired — fall back to invite link
            logger.warning(f"approve_chat_join_request failed for {chat_id}: {e} — sending invite link")
            set_join_request(chat_id, False)
            await _send_invite_link(bot, chat_id)
        except TelegramError as e:
            logger.warning(f"Could not approve join request for {chat_id}: {e}")
    else:
        # Path B: no pending join request — send a fresh invite link
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
                f"<i>Tap it to join the channel now.</i>"
            ),
            parse_mode="HTML",
        )
        logger.info(f"Invite link sent to {chat_id}")
    except TelegramError as e:
        logger.warning(f"Could not create invite link for {chat_id}: {e}")
