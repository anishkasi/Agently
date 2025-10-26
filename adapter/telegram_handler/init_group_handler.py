"""Handler for initializing a Telegram group with MyAgent."""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from adapter.telegram_handler.decorators import admin_only
from service.group.group_service import GroupService
from service.group.user_service import UserService
from core.di import container


async def init_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initializes the group, syncs members, and sets up configuration."""
    chat = update.effective_chat
    chat_id = chat.id
    chat_name = chat.title or "Unnamed Group"

    # Get services from DI container
    group_service: GroupService = container.get("group_service")
    user_service: UserService = container.get("user_service")

    # Step 1️⃣: Ensure group record exists
    group = await group_service.get_or_create_group(chat_id, chat_name)

    # Step 2️⃣: Sync all current admins/members
    await user_service.sync_all_members(context, chat_id, group.id)

    # Step 3️⃣: Do not create config here; /config will handle creating on save

    # Step 4️⃣: Confirm success
    await update.message.reply_text(
        f"✅ Group *{chat_name}* successfully initialized!\n"
        f"Members synced and configuration ready.",
        parse_mode="Markdown",
    )


def register_init_group_handler(app):
    """Register the /init_group command handler."""
    app.add_handler(CommandHandler("init_group", admin_only()(init_group_command)))

