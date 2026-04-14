"""
Subscription Bot — Entry Point
================================
Runs two services on the same asyncio event loop:

  1. Telegram bot (long-polling)
       /start   — subscribe (shows Buy button when FREE_ACCESS=false)
       /stop    — unsubscribe
       /status  — check subscription + expiry
       /buy     — send Stars invoice
       /stats   — admin: subscriber counts

  2. aiohttp HTTP server
       POST /broadcast  { "message": "<HTML>" }  → fan-out to paid subscribers
       GET  /health                               → health check + subscriber count

Environment variables (set in .env or Railway dashboard):
  SUB_BOT_TOKEN      Telegram bot token (required)
  SUB_BOT_API_KEY    Shared secret for /broadcast endpoint (recommended)
  PORT               HTTP port — set automatically by Railway (default: 8080)
  SUB_BOT_HOST       HTTP bind address  (default: 0.0.0.0)
  ADMIN_CHAT_ID      Telegram chat-id allowed to run /stats
  FREE_ACCESS        true = no payment needed  (default: false)
  SUB_PRICE_STARS    Stars per subscription period  (default: 100)
  SUB_DURATION_DAYS  Days per period               (default: 30)
  CHANNEL_NAME       Display name shown in messages (default: Futures Signals)
  DB_PATH            SQLite path  (default: subscribers.db)
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from src.db import init_db
from src.handlers import cmd_start, cmd_stop, cmd_status, cmd_stats
from src.payments import cmd_buy, cb_buy, pre_checkout, successful_payment
from src.server import make_app


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging():
    Path("logs").mkdir(exist_ok=True)
    fmt      = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt=date_fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/subbot.log", encoding="utf-8"),
        ],
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run():
    setup_logging()
    load_dotenv()
    logger = logging.getLogger("subbot")

    token   = os.getenv("SUB_BOT_TOKEN", "").strip()
    api_key = os.getenv("SUB_BOT_API_KEY", "").strip()
    host    = os.getenv("SUB_BOT_HOST", "0.0.0.0")
    # Railway injects PORT; fall back to SUB_BOT_PORT then 8080
    port    = int(os.getenv("PORT") or os.getenv("SUB_BOT_PORT") or "8080")

    if not token:
        logger.error("SUB_BOT_TOKEN is not set — exiting.")
        sys.exit(1)

    init_db()
    logger.info("Database ready")

    # ------------------------------------------------------------------
    # Telegram application
    # ------------------------------------------------------------------
    tg_app = Application.builder().token(token).build()

    # Commands
    tg_app.add_handler(CommandHandler("start",  cmd_start))
    tg_app.add_handler(CommandHandler("stop",   cmd_stop))
    tg_app.add_handler(CommandHandler("status", cmd_status))
    tg_app.add_handler(CommandHandler("buy",    cmd_buy))
    tg_app.add_handler(CommandHandler("stats",  cmd_stats))

    # Inline "Buy Subscription" button callback
    tg_app.add_handler(CallbackQueryHandler(cb_buy, pattern="^buy$"))

    # Payment flow
    tg_app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    tg_app.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment)
    )

    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started")

    # ------------------------------------------------------------------
    # HTTP server
    # ------------------------------------------------------------------
    web_app = make_app(tg_app.bot, api_key)
    runner  = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"HTTP server listening on {host}:{port}")

    # ------------------------------------------------------------------
    # Run until interrupted
    # ------------------------------------------------------------------
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    try:
        import signal as _signal
        loop.add_signal_handler(_signal.SIGINT,  lambda: stop_event.set())
        loop.add_signal_handler(_signal.SIGTERM, lambda: stop_event.set())
    except NotImplementedError:
        pass  # Windows — Ctrl+C raises KeyboardInterrupt instead

    logger.info("Subscription bot is running. Press Ctrl+C to stop.")
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------
    logger.info("Shutting down...")
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()
    await runner.cleanup()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(run())
