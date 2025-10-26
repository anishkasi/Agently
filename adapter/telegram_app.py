"""
Telegram bot webhook application with health endpoint.

This module sets up the Telegram bot to run behind a webhook server,
registers all handlers, and exposes a /health endpoint for container health checks.
"""

import asyncio
import logging
from aiohttp import web
from telegram.ext import ApplicationBuilder

from core import settings
from core.logging import configure_json_logging
from adapter.cache.redis_cache import get_redis
from adapter.telegram_handler import (
    register_config_handlers,
    register_init_group_handler,
    register_add_context_handlers,
    register_message_handler,
)

logger = logging.getLogger(__name__)


async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint for container orchestration."""
    try:
        r = await get_redis(settings.REDIS_URL)
        pong = await r.ping()
        if pong:
            return web.json_response({"status": "ok"}, status=200)
    except Exception as e:
        logger.error(f"Health check failed: {e}")
    return web.json_response({"status": "degraded"}, status=503)


async def run_webhook_app() -> None:
    """
    Start Telegram bot with webhook server and health endpoint.
    
    This function:
    1. Configures structured JSON logging
    2. Initializes the Telegram Application
    3. Registers all handlers (commands, conversations, messages)
    4. Sets up aiohttp web server with /health and /telegram endpoints
    5. Runs indefinitely until interrupted
    """
    configure_json_logging()
    logger.info("🚀 Starting MyAgent Telegram Bot (webhook mode)")

    # Build Telegram Application
    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()
    
    # Post-init: Initialize Redis cache
    async def _post_init(application):
        await get_redis(settings.REDIS_URL)
        logger.info("✅ Redis cache initialized")
    app.post_init = _post_init

    # Register all handlers
    register_config_handlers(app)
    register_init_group_handler(app)
    register_add_context_handlers(app)
    register_message_handler(app)  # Main message handler (spam → router → rag)
    logger.info("✅ All handlers registered")

    # Create aiohttp web server
    web_app = web.Application()
    web_app.add_routes([web.get("/health", health_handler)])

    async def on_startup(_):
        """Initialize bot and set webhook on startup."""
        await app.initialize()
        await app.start()
        webhook_url = f"{settings.WEBHOOK_PUBLIC_URL}/telegram"
        await app.bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook set to {webhook_url}")

    async def on_cleanup(_):
        """Cleanup bot resources on shutdown."""
        await app.stop()
        await app.shutdown()
        logger.info("👋 Bot shutdown complete")

    web_app.on_startup.append(on_startup)
    web_app.on_cleanup.append(on_cleanup)

    # Telegram webhook update handler
    async def telegram_webhook_handler(request: web.Request) -> web.Response:
        """Handle incoming webhook updates from Telegram."""
        try:
            update_data = await request.json()
            from telegram import Update
            update = Update.de_json(update_data, app.bot)
            await app.process_update(update)
            return web.Response(status=200)
        except Exception as e:
            logger.error(f"Error processing webhook update: {e}")
            return web.Response(status=500)
    
    web_app.router.add_route("POST", "/telegram", telegram_webhook_handler)

    # Start web server
    runner = web.AppRunner(web_app)
    await runner.setup()
    listen_host = getattr(settings, "WEBHOOK_LISTEN", "0.0.0.0")
    listen_port = getattr(settings, "WEBHOOK_PORT", 8080)
    site = web.TCPSite(runner, host=listen_host, port=listen_port)
    logger.info(f"🌐 Starting webhook server on {listen_host}:{listen_port}")
    await site.start()
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Received shutdown signal")
        raise


if __name__ == "__main__":
    asyncio.run(run_webhook_app())


