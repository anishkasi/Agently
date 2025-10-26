"""Handler for bot configuration via /config command."""

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

from adapter.telegram_handler.decorators import admin_only
from service.group.config_service import ConfigService
from core.di import container
from adapter.cache.redis_cache import set_group_config
import logging

logger = logging.getLogger(__name__)

# Conversation states
MENU, EDIT_THRESHOLD, EDIT_SPAM_RULES, EDIT_GROUP_DESC = range(4)


async def _get_pending_cfg(context: ContextTypes.DEFAULT_TYPE, chat_id: int, chat_name: str):
    """Load pending config from chat_data or seed defaults if no DB config exists."""
    config_service: ConfigService = container.get("config_service")
    
    pending = context.chat_data.get("pending_cfg")
    if not pending or pending.get("chat_id") != chat_id:
        cfg = await config_service.get_group_config(chat_id, chat_name)
        if cfg:
            pending = {
                "chat_id": chat_id,
                "personality": cfg.personality,
                "spam_confidence_threshold": cfg.spam_confidence_threshold,
                "spam_rules": cfg.spam_rules or "",
                "group_description": getattr(cfg, "group_description", "") or "",
                "moderation_features": dict(cfg.moderation_features or {}),
            }
        else:
            # Seed defaults (not persisted until Save)
            pending = {
                "chat_id": chat_id,
                "personality": "neutral",
                "spam_confidence_threshold": 0.7,
                "spam_rules": "",
                "group_description": "",
                "moderation_features": {
                    "spam_detection": True,
                    "harmful_intent": False,
                    "fud_filtering": True,
                    "nsfw_detection": False,
                },
            }
        context.chat_data["pending_cfg"] = pending
    return pending


async def render_config_menu(chat_id, chat_name, context: ContextTypes.DEFAULT_TYPE):
    """Render the main configuration menu dynamically using pending state."""
    pending = await _get_pending_cfg(context, chat_id, chat_name)
    if not pending:
        return None, None

    features = pending.get("moderation_features", {})
    feature_icons = lambda key: "✅" if features.get(key) else "❌"

    text = (
        f"🛠️ *Bot Configuration*\n\n"
        f"🎭 *Tone:* {str(pending.get('personality', 'neutral')).title()}\n"
        f"📈 *Spam Confidence:* {pending.get('spam_confidence_threshold')}\n"
        f"🧾 *Spam Rules:* (tap below to edit)\n"
        f"📝 *Group Description:* (tap below to edit)\n"
        f"🧠 *Moderation Features:*\n"
        f"{feature_icons('spam_detection')} Spam Detection  "
        f"{feature_icons('fud_filtering')} FUD Filter\n"
        f"{feature_icons('harmful_intent')} Harmful Intent  "
        f"{feature_icons('nsfw_detection')} NSFW\n"
        "\nSelect an option below to edit:"
    )

    keyboard = [
        [
            InlineKeyboardButton("Change Tone 🔁", callback_data="config_tone"),
            InlineKeyboardButton("Edit Threshold 📈", callback_data="config_edit_threshold"),
        ],
        [
            InlineKeyboardButton("Edit Rules ✏️", callback_data="config_edit_rules"),
            InlineKeyboardButton("Edit Group Description 📝", callback_data="config_edit_group_desc"),
            InlineKeyboardButton("Toggle Features ⚙️", callback_data="config_toggle_features"),
        ],
        [
            InlineKeyboardButton("Cancel ❌", callback_data="config_cancel"),
            InlineKeyboardButton("Save ✅", callback_data="config_save"),
        ],
    ]
    return text, InlineKeyboardMarkup(keyboard)


@admin_only()
async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display config menu directly in the group chat (no DM redirect)."""
    chat = update.effective_chat
    chat_id = chat.id
    chat_name = chat.title or "Unnamed Group"

    # Seed pending state from DB if first time
    pending = await _get_pending_cfg(context, chat_id, chat_name)
    if not pending:
        # Delete user command message
        try:
            if update.message:
                await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        except Exception:
            pass
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Failed to load configuration.")
        return ConversationHandler.END

    # Delete user command message
    try:
        if update.message:
            await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except Exception:
        pass

    text, keyboard = await render_config_menu(chat_id, chat_name, context)
    sent = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=keyboard)
    # Track the menu message id so subsequent edits can update the same message
    context.user_data["config_msg_id"] = sent.message_id

    return MENU


@admin_only()
async def handle_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks for configuration menu."""
    query = update.callback_query
    await query.answer()
    chat = query.message.chat
    chat_name = chat.title or "Unknown Group"
    pending = await _get_pending_cfg(context, chat.id, chat_name)
    if not pending:
        await query.edit_message_text("⚠️ No config found. Please initialize the group first.")
        return ConversationHandler.END

    config_service: ConfigService = container.get("config_service")

    # 1️⃣ Tone cycle
    if query.data == "config_tone":
        cycle = {"neutral": "friendly", "friendly": "strict", "strict": "neutral"}
        current = str(pending.get("personality", "neutral"))
        next_tone = cycle.get(current, "neutral")
        pending["personality"] = next_tone
        # Update pending_cfg in chat_data
        context.chat_data["pending_cfg"] = pending
        text, keyboard = await render_config_menu(chat.id, chat_name, context)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

    # 2️⃣ Spam Confidence Threshold (conversation)
    elif query.data == "config_edit_threshold":
        await query.edit_message_text(
            "📊 Please enter a spam confidence threshold between 0 and 1 (e.g. 0.75):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="config_back")]])
        )
        context.user_data["chat_name"] = chat_name
        context.user_data["config_msg_id"] = query.message.message_id
        return EDIT_THRESHOLD

    # 3️⃣ Spam Rules (conversation)
    elif query.data == "config_edit_rules":
        await query.edit_message_text(
            "✏️ Please send the new spam detection rules (multi-line allowed):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="config_back")]])
        )
        context.user_data["chat_name"] = chat_name
        context.user_data["config_msg_id"] = query.message.message_id
        return EDIT_SPAM_RULES

    elif query.data == "config_edit_group_desc":
        await query.edit_message_text(
            "📝 Please send the new group description (multi-line allowed):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="config_back")]])
        )
        context.user_data["chat_name"] = chat_name
        context.user_data["config_msg_id"] = query.message.message_id
        return EDIT_GROUP_DESC

    # 4️⃣ Toggle Moderation Features
    elif query.data == "config_toggle_features":
        await render_feature_menu(update, context)
        return MENU

    elif query.data == "config_back":
        text, keyboard = await render_config_menu(chat.id, chat_name, context)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        return MENU

    elif query.data == "config_cancel":
        # Discard pending changes
        context.chat_data.pop("pending_cfg", None)
        await query.edit_message_text("❌ Changes discarded.")
        return ConversationHandler.END

    # 5️⃣ Save
    elif query.data == "config_save":
        # Persist pending changes to DB (create if missing)
        existing = await config_service.get_group_config(chat.id, chat_name)
        if existing:
            await config_service.update_config_field(existing.id, "personality", pending.get("personality"))
            await config_service.update_config_field(existing.id, "spam_confidence_threshold", pending.get("spam_confidence_threshold"))
            await config_service.update_config_field(existing.id, "spam_rules", pending.get("spam_rules", ""))
            await config_service.update_config_field(existing.id, "group_description", pending.get("group_description", ""))
            await config_service.update_config_field(existing.id, "moderation_features", pending.get("moderation_features", {}))
            
            # Force refresh the cache with all updated values
            logger.info(f"[ConfigHandler] Refreshing cache for group {chat.id} with new config")
            try:
                await set_group_config(chat.id, {
                    "id": existing.id,
                    "group_id": existing.group_id,
                    "group_description": pending.get("group_description", ""),
                    "spam_sensitivity": existing.spam_sensitivity,
                    "spam_confidence_threshold": pending.get("spam_confidence_threshold"),
                    "spam_rules": pending.get("spam_rules", ""),
                    "rag_enabled": existing.rag_enabled,
                    "personality": pending.get("personality"),
                    "moderation_features": pending.get("moderation_features", {}),
                    "tools_enabled": existing.tools_enabled,
                    "last_updated": None,
                })
                logger.info(f"[ConfigHandler] Cache refreshed successfully with threshold={pending.get('spam_confidence_threshold')}, rules={pending.get('spam_rules', '')[:50]}")
            except Exception as cache_err:
                logger.error(f"[ConfigHandler] Failed to refresh cache: {cache_err}")
        else:
            await config_service.create_group_config(chat.id, chat_name, pending)
        # Clear pending state
        context.chat_data.pop("pending_cfg", None)
        await query.edit_message_text("✅ Configuration saved successfully!")
        return ConversationHandler.END

    return MENU


async def render_feature_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Render the moderation feature toggle submenu."""
    chat = update.callback_query.message.chat
    chat_name = chat.title or "Unknown Group"
    pending = await _get_pending_cfg(context, chat.id, chat_name)
    features = pending.get("moderation_features", {})
    def button_text(key, label):
        return f"{'✅' if features.get(key) else '❌'} {label}"

    keyboard = [
        [
            InlineKeyboardButton(button_text("spam_detection", "Spam Detection"), callback_data="feature_spam_detection"),
            InlineKeyboardButton(button_text("fud_filtering", "FUD Filter"), callback_data="feature_fud_filtering"),
        ],
        [
            InlineKeyboardButton(button_text("harmful_intent", "Harmful Intent"), callback_data="feature_harmful_intent"),
            InlineKeyboardButton(button_text("nsfw_detection", "NSFW"), callback_data="feature_nsfw_detection"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="config_back")],
    ]

    text = "🧠 *Moderation Features*\n\nTap to toggle features below:"
    await update.callback_query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


@admin_only()
async def handle_feature_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle feature toggle button presses."""
    query = update.callback_query
    await query.answer()
    chat = query.message.chat
    chat_name = chat.title or "Unknown Group"
    pending = await _get_pending_cfg(context, chat.id, chat_name)
    features = pending.get("moderation_features", {})

    feature_map = {
        "feature_spam_detection": "spam_detection",
        "feature_harmful_intent": "harmful_intent",
        "feature_fud_filtering": "fud_filtering",
        "feature_nsfw_detection": "nsfw_detection",
    }

    if query.data in feature_map:
        key = feature_map[query.data]
        features[key] = not features.get(key, False)
        pending["moderation_features"] = features
        # Update pending_cfg in chat_data
        context.chat_data["pending_cfg"] = pending
        # Re-render same screen
        await render_feature_menu(update, context)

    return MENU


async def save_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle numeric spam threshold input."""
    # Delete the user's message to avoid exposing config inputs
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception:
        pass
    try:
        value = float(update.message.text.strip())
        if not 0 <= value <= 1:
            raise ValueError
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Invalid input. Please enter a number between 0 and 1.")
        return EDIT_THRESHOLD

    chat = update.effective_chat
    chat_name = context.user_data.get("chat_name") or (chat.title or "Unknown Group")
    pending = await _get_pending_cfg(context, chat.id, chat_name)
    pending["spam_confidence_threshold"] = value
    # Update pending_cfg in chat_data
    context.chat_data["pending_cfg"] = pending
    text, keyboard = await render_config_menu(chat.id, chat_name, context)
    msg_id = context.user_data.get("config_msg_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                text=text,
                chat_id=chat.id,
                message_id=msg_id,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception:
            # Fallback to sending a fresh menu message if edit fails
            sent = await context.bot.send_message(chat_id=chat.id, text=text, parse_mode="Markdown", reply_markup=keyboard)
            context.user_data["config_msg_id"] = sent.message_id
    else:
        sent = await context.bot.send_message(chat_id=chat.id, text=text, parse_mode="Markdown", reply_markup=keyboard)
        context.user_data["config_msg_id"] = sent.message_id
    return MENU


async def save_spam_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle multi-line spam rule input."""
    # Delete the user's message to avoid exposing config inputs
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception:
        pass
    text = update.message.text.strip()
    chat = update.effective_chat
    chat_name = context.user_data.get("chat_name") or (chat.title or "Unknown Group")
    pending = await _get_pending_cfg(context, chat.id, chat_name)
    pending["spam_rules"] = text
    # Update pending_cfg in chat_data
    context.chat_data["pending_cfg"] = pending
    main_text, keyboard = await render_config_menu(chat.id, chat_name, context)
    msg_id = context.user_data.get("config_msg_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                text=main_text,
                chat_id=chat.id,
                message_id=msg_id,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception:
            sent = await context.bot.send_message(chat_id=chat.id, text=main_text, parse_mode="Markdown", reply_markup=keyboard)
            context.user_data["config_msg_id"] = sent.message_id
    else:
        sent = await context.bot.send_message(chat_id=chat.id, text=main_text, parse_mode="Markdown", reply_markup=keyboard)
        context.user_data["config_msg_id"] = sent.message_id
    return MENU


async def save_group_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle multi-line group description input."""
    # Delete the user's message to avoid exposing config inputs
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception:
        pass
    text = update.message.text.strip()
    chat = update.effective_chat
    chat_name = context.user_data.get("chat_name") or (chat.title or "Unknown Group")
    pending = await _get_pending_cfg(context, chat.id, chat_name)
    pending["group_description"] = text
    # Update pending_cfg in chat_data
    context.chat_data["pending_cfg"] = pending
    main_text, keyboard = await render_config_menu(chat.id, chat_name, context)
    msg_id = context.user_data.get("config_msg_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                text=main_text,
                chat_id=chat.id,
                message_id=msg_id,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception:
            sent = await context.bot.send_message(chat_id=chat.id, text=main_text, parse_mode="Markdown", reply_markup=keyboard)
            context.user_data["config_msg_id"] = sent.message_id
    else:
        sent = await context.bot.send_message(chat_id=chat.id, text=main_text, parse_mode="Markdown", reply_markup=keyboard)
        context.user_data["config_msg_id"] = sent.message_id
    return MENU


# ConversationHandler setup
config_conversation = ConversationHandler(
    entry_points=[CommandHandler("config", config_command)],
    states={
        MENU: [
            CallbackQueryHandler(handle_config_callback, pattern="^config_"),
            CallbackQueryHandler(handle_feature_toggle, pattern="^feature_"),
        ],
        EDIT_THRESHOLD: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_threshold)],
        EDIT_SPAM_RULES: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_spam_rules)],
        EDIT_GROUP_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_group_description)],
    },
    fallbacks=[],
)


def register_config_handlers(app):
    """Register the /config conversation handler."""
    app.add_handler(config_conversation)

