"""Development runner using polling mode (no webhook needed).

This is easier for local testing since it doesn't require a public URL.
Use this for development and testing. Use main.py for production (webhook mode).
"""

import asyncio
import logging
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


def main():
    """Run bot in polling mode for local development."""
    configure_json_logging()
    logger.info("🚀 Starting MyAgent Telegram Bot (POLLING MODE - Development)")

    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not set in environment")
        return

    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Initialize Redis
    async def _post_init(application):
        await get_redis(settings.REDIS_URL)
        logger.info("✅ Redis cache initialized")
    
    app.post_init = _post_init

    # Register all handlers
    register_config_handlers(app)
    register_init_group_handler(app)
    register_add_context_handlers(app)
    register_message_handler(app)
    logger.info("✅ All handlers registered")

    # Start polling (run_polling manages its own event loop)
    logger.info("📡 Starting polling (press Ctrl+C to stop)...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()

