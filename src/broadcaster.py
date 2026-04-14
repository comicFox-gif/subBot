"""
Broadcaster — sends a Telegram HTML message to every eligible subscriber.

FREE_ACCESS=true  → sends to all active subscribers
FREE_ACCESS=false → sends only to subscribers with an active paid subscription

Respects Telegram's ~30 msg/sec rate limit by spacing sends 50 ms apart.
Silently deactivates subscribers who have blocked the bot (Forbidden error).
"""

import asyncio
import logging

from telegram import Bot
from telegram.error import Forbidden, TelegramError

from .db import get_broadcast_targets, unsubscribe

logger = logging.getLogger("subbot.broadcaster")

_SEND_DELAY = 0.05  # 50 ms between sends → ~20/sec (well under 30/sec cap)


async def broadcast(bot: Bot, message: str) -> tuple[int, int]:
    """
    Broadcast *message* to all eligible subscribers.
    Returns (success_count, failed_count).
    """
    targets = get_broadcast_targets()
    if not targets:
        logger.info("Broadcast skipped — no eligible subscribers")
        return 0, 0

    success = 0
    failed  = 0

    for chat_id in targets:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            success += 1
        except Forbidden:
            # User blocked the bot — deactivate silently
            logger.info(f"Deactivating blocked subscriber {chat_id}")
            unsubscribe(chat_id)
            failed += 1
        except TelegramError as e:
            logger.warning(f"Failed to send to {chat_id}: {e}")
            failed += 1

        await asyncio.sleep(_SEND_DELAY)

    logger.info(f"Broadcast complete — sent: {success}, failed: {failed}")
    return success, failed
