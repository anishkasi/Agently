"""Handler for adding context to a group via /add_context command."""

import asyncio
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
from service.rag_service import RAGService
from core.di import container

# Conversation states
MENU, AWAIT_FILE, AWAIT_LINK, AWAIT_TEXT, REVIEW = range(5)


def _menu_keyboard() -> InlineKeyboardMarkup:
    """Return the main menu keyboard for context ingestion."""
    keyboard = [
        [
            InlineKeyboardButton("📄 Upload File", callback_data="ctx_upload_file"),
            InlineKeyboardButton("🔗 Add Link", callback_data="ctx_add_link"),
        ],
        [InlineKeyboardButton("✏️ Add Text", callback_data="ctx_add_text")],
    ]
    return InlineKeyboardMarkup(keyboard)


def _awaiting_keyboard() -> InlineKeyboardMarkup:
    """Return a keyboard with a back button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="ctx_back_menu")],
    ])


def _review_keyboard() -> InlineKeyboardMarkup:
    """Return a keyboard for reviewing pending context items."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Save ✅", callback_data="ctx_save"),
            InlineKeyboardButton("➕ Add More", callback_data="ctx_add_more"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="ctx_back_menu")],
    ])


def _get_pending(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Get or initialize the pending context items dict."""
    return context.chat_data.setdefault("add_ctx", {"mode": None, "items": []})


@admin_only()
async def add_context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /add_context command."""
    chat = update.effective_chat
    if not chat:
        return ConversationHandler.END
    context.chat_data["add_ctx"] = {"mode": None, "items": []}
    text = (
        "📚 *Add Context*\n\n"
        "Choose how you'd like to add context for this group:"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_menu_keyboard())
    return MENU


@admin_only()
async def handle_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle main menu choices (file/link/text)."""
    query = update.callback_query
    await query.answer()
    pending = _get_pending(context)
    if query.data == "ctx_upload_file":
        pending["mode"] = "file"
        await query.edit_message_text("📄 Please upload a file (document/photo/audio/video).", reply_markup=_awaiting_keyboard())
        return AWAIT_FILE
    if query.data == "ctx_add_link":
        pending["mode"] = "link"
        await query.edit_message_text("🔗 Please send a link (URL).", reply_markup=_awaiting_keyboard())
        return AWAIT_LINK
    if query.data == "ctx_add_text":
        pending["mode"] = "text"
        await query.edit_message_text("✏️ Please send the text context.", reply_markup=_awaiting_keyboard())
        return AWAIT_TEXT
    return MENU


@admin_only()
async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle back button to return to main menu."""
    query = update.callback_query
    await query.answer()
    pending = _get_pending(context)
    pending["mode"] = None
    await query.edit_message_text(
        "📚 *Add Context*\n\nChoose how you'd like to add context for this group:",
        parse_mode="Markdown",
        reply_markup=_menu_keyboard(),
    )
    return MENU


@admin_only()
async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file upload messages."""
    msg = update.effective_message
    if not msg:
        return AWAIT_FILE
    # Delete the user's message to avoid persisting context items in chat
    try:
        await context.bot.delete_message(chat_id=msg.chat_id, message_id=msg.message_id)
    except Exception:
        pass
    if not (msg.document or msg.photo or msg.audio or msg.video):
        await context.bot.send_message(chat_id=msg.chat_id, text="⚠️ Not a supported file. Please send a document/photo/audio/video.", reply_markup=_awaiting_keyboard())
        return AWAIT_FILE

    pending = _get_pending(context)
    item = {"type": "file"}
    if msg.document:
        item.update({"file_kind": "document", "file_id": msg.document.file_id, "name": msg.document.file_name})
    elif msg.photo:
        item.update({"file_kind": "photo", "file_id": msg.photo[-1].file_id})
    elif msg.audio:
        item.update({"file_kind": "audio", "file_id": msg.audio.file_id})
    elif msg.video:
        item.update({"file_kind": "video", "file_id": msg.video.file_id})
    pending["items"].append(item)

    await context.bot.send_message(chat_id=msg.chat_id, text="✅ Received file — ready to save.", reply_markup=_review_keyboard())
    return REVIEW


@admin_only()
async def receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle link input messages."""
    text = (update.message.text or "").strip()
    # Delete the user's message
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception:
        pass
    url = None
    if update.message.entities:
        for ent in update.message.entities:
            if ent.type == "url":
                url = text[ent.offset : ent.offset + ent.length]
                break
            if ent.type == "text_link" and getattr(ent, "url", None):
                url = ent.url
                break
    if not url and (text.startswith("http://") or text.startswith("https://")):
        url = text
    if not url:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Didn't detect a valid URL. Please send a link.", reply_markup=_awaiting_keyboard())
        return AWAIT_LINK

    pending = _get_pending(context)
    pending["items"].append({"type": "link", "url": url})
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Received link — ready to save.", reply_markup=_review_keyboard())
    return REVIEW


@admin_only()
async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input messages."""
    text = (update.message.text or "").strip()
    # Delete the user's message
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception:
        pass
    if not text:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Please send some text.", reply_markup=_awaiting_keyboard())
        return AWAIT_TEXT
    pending = _get_pending(context)
    pending["items"].append({"type": "text", "text": text})
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Received text — ready to save.", reply_markup=_review_keyboard())
    return REVIEW


@admin_only()
async def receive_wrong_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle non-file messages in file mode."""
    # Delete the user's message
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception:
        pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Not a supported file. Please send a document/photo/audio/video.", reply_markup=_awaiting_keyboard())
    return AWAIT_FILE


@admin_only()
async def receive_wrong_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle non-text messages in link mode."""
    # Delete the user's message
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception:
        pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ This mode expects a URL. Please send a link.", reply_markup=_awaiting_keyboard())
    return AWAIT_LINK


@admin_only()
async def receive_wrong_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle non-text messages in text mode."""
    # Delete the user's message
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception:
        pass
    await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ This mode expects plain text. Please send text.", reply_markup=_awaiting_keyboard())
    return AWAIT_TEXT


@admin_only()
async def handle_review_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle review screen choices (save/add more/back)."""
    query = update.callback_query
    await query.answer()
    pending = _get_pending(context)
    mode = pending.get("mode")
    
    if query.data == "ctx_save":
        # Ingest all pending items (extract → embed → store)
        chat = query.message.chat
        uploader_id = query.from_user.id
        bot_token = context.bot.token
        
        # Get RAG service from DI container
        rag_service: RAGService = container.get("rag_service")
        
        tasks = []
        for item in pending.get("items", []):
            if item.get("type") == "file":
                tasks.append(rag_service.process_file_context(chat.id, uploader_id, item.get("file_id"), item.get("name"), bot_token))
            elif item.get("type") == "link":
                tasks.append(rag_service.process_link_context(chat.id, uploader_id, item.get("url")))
            elif item.get("type") == "text":
                tasks.append(rag_service.process_text_context(chat.id, uploader_id, item.get("text")))
        if tasks:
            try:
                await asyncio.gather(*tasks)
                await query.edit_message_text("✅ Context successfully added to group knowledge base.")
            except Exception as e:
                await query.edit_message_text(f"⚠️ Failed to add context: {e}")
        else:
            await query.edit_message_text("⚠️ No items to save.")
        context.chat_data["add_ctx"] = {"mode": None, "items": []}
        return ConversationHandler.END
    
    if query.data == "ctx_add_more":
        if mode == "file":
            await query.edit_message_text("📄 Please upload another file.", reply_markup=_awaiting_keyboard())
            return AWAIT_FILE
        if mode == "link":
            await query.edit_message_text("🔗 Please send another link.", reply_markup=_awaiting_keyboard())
            return AWAIT_LINK
        if mode == "text":
            await query.edit_message_text("✏️ Please send more text.", reply_markup=_awaiting_keyboard())
            return AWAIT_TEXT
    
    if query.data == "ctx_back_menu":
        return await back_to_menu(update, context)
    
    return REVIEW


add_context_conversation = ConversationHandler(
    entry_points=[CommandHandler("add_context", add_context_command)],
    states={
        MENU: [CallbackQueryHandler(handle_menu_choice, pattern="^ctx_(upload_file|add_link|add_text)$")],
        AWAIT_FILE: [
            CallbackQueryHandler(back_to_menu, pattern="^ctx_back_menu$"),
            MessageHandler(~filters.COMMAND & (filters.Document.ALL | filters.PHOTO | filters.AUDIO | filters.VIDEO), receive_file),
            MessageHandler(~filters.COMMAND & ~(filters.Document.ALL | filters.PHOTO | filters.AUDIO | filters.VIDEO), receive_wrong_file),
        ],
        AWAIT_LINK: [
            CallbackQueryHandler(back_to_menu, pattern="^ctx_back_menu$"),
            MessageHandler(~filters.COMMAND & filters.TEXT, receive_link),
            MessageHandler(~filters.COMMAND & ~filters.TEXT, receive_wrong_link),
        ],
        AWAIT_TEXT: [
            CallbackQueryHandler(back_to_menu, pattern="^ctx_back_menu$"),
            MessageHandler(~filters.COMMAND & filters.TEXT, receive_text),
            MessageHandler(~filters.COMMAND & ~filters.TEXT, receive_wrong_text),
        ],
        REVIEW: [
            CallbackQueryHandler(handle_review_choice, pattern="^ctx_(save|add_more|back_menu)$"),
        ],
    },
    fallbacks=[],
)


def register_add_context_handlers(app):
    """Register the /add_context conversation handler."""
    app.add_handler(add_context_conversation)

