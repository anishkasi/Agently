"""Decorators for Telegram handler access control."""

from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatType


async def is_admin(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if a user is an admin or owner of the chat."""
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ("administrator", "creator")
    except Exception as e:
        print(f"⚠️ Failed to check admin status: {e}")
        return False


def admin_only():
    """Decorator that restricts handler execution to group admins."""
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            # Determine where the request came from (message vs callback)
            if update.effective_user is None or update.effective_chat is None:
                return

            user_id = update.effective_user.id
            chat_id = update.effective_chat.id

            if update.effective_chat.type == ChatType.PRIVATE:
                return await func(update, context, *args, **kwargs)

            if not await is_admin(user_id, chat_id, context):
                try:
                    # Respond appropriately whether it's a callback or text
                    if update.callback_query:
                        await update.callback_query.answer("🚫 Only admins can perform this action.", show_alert=True)
                    elif update.message:
                        await update.message.reply_text("🚫 Only admins can use this command.")
                except Exception as e:
                    print(f"Error sending admin warning: {e}")
                return  # stop further execution

            # Proceed if admin
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator

