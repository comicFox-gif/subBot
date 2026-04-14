"""
HTTP signal server
-------------------
Listens for POST /broadcast from the main Futures signals bot.
The main bot sends pre-formatted HTML messages; this server
queues them for broadcast to all Telegram subscribers.

Auth: optional shared secret via X-API-Key header.
      Set SUB_BOT_API_KEY env var on both sides.

Endpoints:
  POST /broadcast   { "message": "<HTML text>" }  → queues broadcast
  GET  /health      → {"status": "ok", "subscribers": N}
"""

import asyncio
import logging

from aiohttp import web

from .broadcaster import broadcast
from .db import count_active

logger = logging.getLogger("subbot.server")


def make_app(bot, api_key: str) -> web.Application:
    app = web.Application()

    # ------------------------------------------------------------------
    # POST /broadcast
    # ------------------------------------------------------------------
    async def handle_broadcast(request: web.Request) -> web.Response:
        # Optional API key auth
        if api_key:
            provided = request.headers.get("X-API-Key", "")
            if provided != api_key:
                logger.warning(f"Unauthorized broadcast attempt from {request.remote}")
                return web.Response(status=401, text="Unauthorized")

        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON body")

        message = (data.get("message") or "").strip()
        if not message:
            return web.Response(status=400, text="Missing or empty 'message' field")

        # Fire and forget — don't block the HTTP response
        asyncio.create_task(broadcast(bot, message))
        logger.info(f"Broadcast queued ({len(message)} chars)")
        return web.json_response({"status": "queued"})

    # ------------------------------------------------------------------
    # GET /health
    # ------------------------------------------------------------------
    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "subscribers": count_active()})

    app.router.add_post("/broadcast", handle_broadcast)
    app.router.add_get("/health",     handle_health)
    return app
