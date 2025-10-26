"""
MyAgent Telegram Bot - Main Entry Point

This is the production entry point that runs the bot in webhook mode.
For local development with polling, use bot/main.py (legacy).
"""

import asyncio
import sys
from adapter.telegram_app import run_webhook_app


if __name__ == "__main__":
    try:
        asyncio.run(run_webhook_app())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)

