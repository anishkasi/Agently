"""Core message handling logic for all incoming Telegram messages."""

import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.di import container
from adapter.context_builder import build_context
from adapter.cache.redis_cache import (
    get_group_state,
    get_recent_user_group_messages,
    set_task_status,
)
from adapter.db.models import Message
from adapter.telegram_middlewares import require_initialized_and_configured_group
from service.group.group_service import GroupService
from service.group.user_service import UserService
from service.message_service import MessageService
from service.moderation_service import detect_and_treat_spam
from service.router_service import RouterService
from service.rag_service import RAGService

logger = logging.getLogger(__name__)


async def safe_detect_spam(user_id, group_id, payload, bot, ctx=None):
    """
    Wrapper for spam detection that handles errors gracefully and updates message status.
    
    Args:
        user_id: Telegram user ID
        group_id: Telegram chat ID
        payload: Message payload dict
        bot: Telegram bot instance
        ctx: Optional pre-built ContextBundle
        
    Returns:
        SpamVerdict or None if error
    """
    try:
        verdict = await detect_and_treat_spam(user_id, group_id, payload, bot, ctx=ctx)
        message_id = payload.get("id")
        if verdict and message_id:
            # Only flip messages.is_spam when verdict is True (defaults to False)
            if bool(getattr(verdict, "spam", False)):
                try:
                    async with container.get_async("db_session") as session:
                        result = await session.execute(
                            select(Message).where(Message.id == message_id)
                        )
                        db_msg = result.scalar_one_or_none()
                        if db_msg:
                            db_msg.is_spam = True
                            await session.commit()
                except Exception as db_err:
                    logger.error(f"Failed to set spam flag for message {message_id}: {db_err}")

                # Reflect in cache via task status
                try:
                    await set_task_status(message_id, "spam")
                except Exception as cache_err:
                    logger.error(f"Failed to set task status for message {message_id}: {cache_err}")

                logger.warning(
                    f"Spam detected for user {user_id} in group {group_id} | confidence={getattr(verdict, 'confidence', None)}"
                )
        return verdict
    except Exception as e:
        logger.error(f"Error during spam detection for user {user_id} in group {group_id}: {e}")
        return None


@require_initialized_and_configured_group
async def log_every_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main handler for all non-command messages.
    Logs the message, runs spam detection, routes intents, and answers QnA via RAG.
    """
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    if chat is None or user is None or msg is None:
        return

    # Get services from DI container
    group_service: GroupService = container.get("group_service")
    user_service: UserService = container.get("user_service")
    message_service: MessageService = container.get("message_service")
    router_service: RouterService = container.get("router_service")
    rag_service: RAGService = container.get("rag_service")

    # Ensure group via cache-first
    group_state = await get_group_state(chat.id)
    if not group_state:
        await group_service.get_or_create_group(chat.id, chat.title or "Unknown Group")

    # Ensure user via cache-first (seen-in-group heuristic)
    seen = await get_recent_user_group_messages(user.id, chat.id, limit=1)
    if not seen:
        created = await user_service.handle_user_join_raw(
            user_id=user.id,
            username=user.username,
            chat_id=chat.id,
            status="member",
            is_bot=getattr(user, "is_bot", False),
        )
        if not created:
            return

    # Determine message type and content
    message_type = "text"
    content = None
    caption = None
    all_links = []
    file_id = None

    if msg.text:
        message_type = "text"
        content = msg.text
        # check for links
        if update.message.entities:
            for entity in update.message.entities:
                if entity.type == 'url':
                    # Extract the URL from the message text using offset and length
                    link_text = update.message.text[entity.offset : entity.offset + entity.length]
                    logger.debug(f"Detected plain URL: {link_text}")
                    all_links.append(link_text)
                elif entity.type == 'text_link':
                    # The URL is directly available in the entity's 'url' field
                    logger.debug(f"Detected embedded link with URL: {entity.url}")
                    all_links.append(entity.url)

    elif msg.photo:
        message_type = "image"
        caption = msg.caption
        # get highest resolution photo
        tg_photo = msg.photo[-1]
        file_id = tg_photo.file_id
    elif getattr(msg, "animation", None):
        # Telegram 'animation' is often MP4; prefer using its thumbnail as an image for vision
        anim_mime = getattr(msg.animation, "mime_type", "") or ""
        if anim_mime == "image/gif":
            message_type = "GIF"
            caption = msg.caption
            file_id = msg.animation.file_id
        else:
            # Try to use the animation thumbnail as an image
            thumb = getattr(msg.animation, "thumbnail", None) or getattr(msg.animation, "thumb", None)
            if thumb and getattr(thumb, "file_id", None):
                message_type = "GIF"
                caption = msg.caption
                file_id = thumb.file_id
            else:
                # Fallback to video (not processed by vision yet)
                message_type = "video"
                caption = msg.caption
    elif msg.voice or msg.audio:
        message_type = "audio"
        caption = msg.caption
        file_id = (msg.voice.file_id if msg.voice else msg.audio.file_id)
    elif msg.video:
        message_type = "video"
        caption = msg.caption
    elif msg.document:
        # If document is a GIF, also treat as image (first frame)
        mime = getattr(msg.document, "mime_type", "") or ""
        if mime == "image/gif":
            message_type = "GIF"
            caption = msg.caption
            file_id = msg.document.file_id
        else:
            message_type = "document"
            caption = msg.caption

    # Persist message
    saved = await message_service.log_message(
        group_id=chat.id,
        user_id=user.id,
        message_type=message_type,
        content=content,
        caption=caption,
        meta={"tg_message_id": msg.message_id},
    )

    # For text messages, run spam detection immediately
    if message_type == "text":
        new_msg_payload = {
            "id": saved.id,
            "type": message_type,
            "text": (content or caption or ""),
            "telegram_message_id": msg.message_id,
            "user_id": user.id,
            "group_id": chat.id,
        }
        
        async def _spam_then_route():
            """Background task: spam detection -> routing -> RAG if QnA."""
            ctx = await build_context(user.id, chat.id, new_msg_payload)
            verdict = await safe_detect_spam(user.id, chat.id, new_msg_payload, context.bot, ctx=ctx)
            if not getattr(verdict, "spam", False):
                result = await router_service.route(ctx)
                if result:
                    logger.info(
                        f"Router intent={result.intent.value} conf={result.confidence:.2f} evidence={result.evidence}"
                    )
                    # If QnA-eligible, answer via RAG and reply in Telegram
                    try:
                        if getattr(result, "intent", None) and result.intent.value == "qna" and bool(result.is_group_qna_eligible):
                            question_text = (content or caption or "").strip()
                            if question_text:
                                rag = await rag_service.answer(group_id=chat.id, question=question_text)
                                logger.info(f"RAG answer: {rag}")
                                if rag and getattr(rag, "answer", None):
                                    await context.bot.send_message(
                                        chat_id=chat.id,
                                        text=rag.answer,
                                        reply_to_message_id=msg.message_id
                                    )
                    except Exception as e:
                        logger.error(f"Failed to send RAG answer: {e}")

        asyncio.create_task(_spam_then_route())

    # If media, add MediaAsset with Telegram file_id for later processing
    if message_type in {"image", "audio", "GIF"} and file_id:
        logger.debug(f"File ID: {file_id}")
        await message_service.add_media_asset(
            message_id=saved.id,
            media_type=("image" if message_type == "GIF" else message_type),
            url="",
            meta={"file_id": file_id},
        )

    # Add links if detected
    if all_links:
        for link in all_links:
            await message_service.add_link(message_id=saved.id, url=link)
    else:
        # If this is a media message with caption, extract links from caption_entities
        if caption and getattr(msg, "caption_entities", None):
            for entity in msg.caption_entities:
                if entity.type == 'url':
                    link_text = caption[entity.offset : entity.offset + entity.length]
                    all_links.append(link_text)
                elif entity.type == 'text_link' and getattr(entity, 'url', None):
                    all_links.append(entity.url)
            logger.debug(f"Caption links: {all_links}")
            for link in all_links:
                await message_service.add_link(message_id=saved.id, url=link)

    # Kick off background enrichment (upload + VLM/STT/link crawl).
    # After enrichment, run spam detection for non-text types when summary/content is available.
    async def _enrich_and_check():
        """Background task: enrich media -> spam detection -> routing -> RAG if QnA."""
        # Perform enrichment first (populates Message.summary/MediaAsset.summary)
        await message_service.parse_message(saved)
        if message_type != "text":
            # Load enriched summary from DB
            enriched_text = None
            try:
                async with container.get_async("db_session") as session:
                    result = await session.execute(
                        select(Message)
                        .options(selectinload(Message.media_assets))
                        .where(Message.id == saved.id)
                    )
                    db_msg = result.scalar_one_or_none()
                    if db_msg:
                        enriched_text = db_msg.summary
                        if not enriched_text and getattr(db_msg, "media_assets", None):
                            for asset in db_msg.media_assets:
                                if getattr(asset, "summary", None):
                                    enriched_text = asset.summary
                                    break
            except Exception as e:
                logger.error(f"Failed to load enriched text: {e}")
            # Fallback to original content/caption if no enrichment text was found
            payload_text = enriched_text or content or caption or ""
            new_msg_payload = {
                "id": saved.id,
                "type": message_type,
                "text": payload_text,
                "telegram_message_id": msg.message_id,
                "user_id": user.id,
                "group_id": chat.id,
            }
            # Build once, then spam → router
            ctx = await build_context(user.id, chat.id, new_msg_payload)
            verdict = await safe_detect_spam(user.id, chat.id, new_msg_payload, context.bot, ctx=ctx)
            if not getattr(verdict, "spam", False):
                result = await router_service.route(ctx)
                if result:
                    logger.info(
                        f"Router intent={result.intent.value} conf={result.confidence:.2f} evidence={result.evidence}"
                    )
                    # If QnA-eligible, answer via RAG and reply in Telegram
                    try:
                        if getattr(result, "intent", None) and result.intent.value == "qna" and bool(result.is_group_qna_eligible):
                            question_text = (payload_text or "").strip()
                            if question_text:
                                rag = await rag_service.answer(group_id=chat.id, question=question_text)
                                if rag and getattr(rag, "answer", None):
                                    await context.bot.send_message(
                                        chat_id=chat.id,
                                        text=rag.answer,
                                        reply_to_message_id=msg.message_id
                                    )
                    except Exception as e:
                        logger.error(f"Failed to send RAG answer: {e}")

    asyncio.create_task(_enrich_and_check())


def register_message_handler(app):
    """Register the main message handler for all non-command messages."""
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, log_every_message))

