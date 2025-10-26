import os
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from core.di import container
from adapter.db.models import Message, MediaAsset, Link
from adapter.storage.storage_client import upload_to_supabase

from adapter.processor.vision import describe_image
from adapter.processor.whisper_stt import transcribe_audio
from adapter.processor.firecrawl import fetch_page_summary
from adapter.cache.redis_cache import (
    append_user_group_message,
    append_user_global_meta,
    append_group_message,
    set_task_status,
    append_user_group_enriched,
)

from core import settings


BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN


class MessageService:
    """Handles message logging and multimodal enrichment using AsyncSessionLocal."""

    def __init__(self):
        pass

    async def log_message(
        self,
        group_id: int,
        user_id: int,
        message_type: str,
        content: str | None = None,
        caption: str | None = None,
        meta: dict | None = None,
    ) -> Message:
        """
        Log a new message and return the Message instance.

        Args:
            group_id: The Telegram chat ID
            user_id: The Telegram user ID
            message_type: The type of the message
            content: The content of the message
            caption: The caption of the message
            meta: The metadata of the message

        Returns:
            The Message instance
        """
        async with container.db() as session:
            msg = Message(
                group_id=group_id,
                user_id=user_id,
                message_type=message_type,
                content=content,
                caption=caption,
                meta=meta or {},
                processed=False,
            )
            session.add(msg)
            await session.commit()
            await session.refresh(msg)
            try:
                await set_task_status(msg.id, "pending")
                payload = {
                    "id": msg.id,
                    "type": msg.message_type,
                    "text": msg.content,
                    "user_id": msg.user_id,
                    "group_id": msg.group_id,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                }
                await append_user_group_message(msg.user_id, msg.group_id, payload)
                await append_user_global_meta(msg.user_id, payload)
                await append_group_message(msg.group_id, payload)
            except Exception:
                pass
            return msg

    async def add_media_asset(
        self,
        message_id: int,
        media_type: str,
        url: str | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
        file_size: int | None = None,
        mime_type: str | None = None,
        duration: float | None = None,
        meta: dict | None = None,
    ) -> MediaAsset:
        """
        Create a MediaAsset row linked to a Message.

        Args:
            message_id: The ID of the message
            media_type: The type of the media
            url: The URL of the media
            width: The width of the media
            height: The height of the media
            file_size: The size of the media
            mime_type: The MIME type of the media
            duration: The duration of the media
            meta: The metadata of the media

        Returns:
            The MediaAsset instance
        """
        async with container.db() as session:
            asset = MediaAsset(
                message_id=message_id,
                media_type=media_type,
                url=url or "",
                width=width,
                height=height,
                file_size=file_size,
                mime_type=mime_type,
                duration=duration,
                meta=meta or {},
                processed=False,
            )
            session.add(asset)
            await session.commit()
            await session.refresh(asset)
            return asset

    async def add_link(self, message_id: int, url: str) -> Link:
        """
        Create a Link row linked to a Message.

        Args:
            message_id: The ID of the message
            url: The URL of the link

        Returns:
            The Link instance
        """
        async with container.db() as session:
            link = Link(
                message_id=message_id,
                url=url,
                processed=False,
            )
            session.add(link)
            await session.commit()
            await session.refresh(link)
            return link

    async def parse_message(self, message: Message):
        """
        Parse a message and return the Message instance.

        Args:
            message: The Message instance

        Returns:
            The Message instance
        """
        async with container.db() as session:
            result = await session.execute(
                select(Message)
                .options(
                    selectinload(Message.media_assets),
                    selectinload(Message.links),
                )
                .where(Message.id == message.id)
            )
            db_message = result.scalar_one_or_none()
            if not db_message:
                return

            if db_message.media_assets and db_message.message_type in {"image", "GIF"}:
                await self._parse_image(session, db_message)

            if db_message.links:
                await self._parse_link(session, db_message)

            if db_message.message_type == "audio":
                await self._parse_audio(session, db_message)

            if db_message.message_type == "text" and not (db_message.links or db_message.media_assets):
                await self._parse_text(session, db_message)

            db_message.processed = True
            await session.commit()
            try:
                media_types_for_enrich = {"image", "audio", "video", "document", "GIF"}
                if db_message.summary and db_message.message_type in media_types_for_enrich:
                    await append_user_group_enriched(
                        db_message.user_id,
                        db_message.group_id,
                        db_message.id,
                        summary=db_message.summary[:300],
                        created_at=db_message.created_at.isoformat() if db_message.created_at else None,
                    )
            except Exception:
                pass

    async def _parse_text(self, session, message: Message):
        message.summary = (message.content or "")[:500]
        message.processed = True
        await session.commit()

    async def _get_telegram_file_url(self, file_id: str) -> str:
        import aiohttp
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        async with aiohttp.ClientSession() as http_session:
            async with http_session.get(api_url) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to get file info from Telegram: {resp.status}")
                data = await resp.json()
                if not data.get("ok"):
                    raise Exception(f"Telegram API error: {data}")
                file_path = data["result"]["file_path"]
                file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                return file_url

    async def _parse_image(self, session, message: Message):
        for asset in list(message.media_assets or []):
            try:
                file_id = asset.meta.get("file_id") if asset.meta else None
                if not file_id:
                    raise Exception("Missing Telegram file_id in media asset metadata.")
                file_url = await self._get_telegram_file_url(file_id)
                supabase_url = await upload_to_supabase(file_url, "image", message.group_id, message.user_id)
                description = describe_image(supabase_url)
                asset.url = supabase_url
                asset.summary = description
                if message.summary:
                    message.summary += f"IMAGE DESCRIPTION: {description}"
                else:
                    message.summary = f"IMAGE DESCRIPTION: {description}"
                asset.processed = True
                await session.commit()
            except Exception as e:
                asset.meta = {"error": str(e)}
                await session.commit()
        message.processed = True
        await session.commit()

    async def _parse_audio(self, session, message: Message):
        for asset in list(message.media_assets or []):
            try:
                file_id = asset.meta.get("file_id") if asset.meta else None
                if not file_id:
                    raise Exception("Missing Telegram file_id in media asset metadata.")
                file_url = await self._get_telegram_file_url(file_id)
                supabase_url = await upload_to_supabase(file_url, "audio", message.group_id, message.user_id)
                transcription = await transcribe_audio(supabase_url)
                asset.url = supabase_url
                asset.transcription = transcription
                asset.summary = transcription[:500]
                asset.processed = True
                message.summary = transcription[:500]
                await session.commit()
            except Exception as e:
                asset.meta = {"error": str(e)}
                await session.commit()
        message.processed = True
        await session.commit()

    async def _parse_link(self, session, message: Message):
        for index, link in enumerate(list(message.links or [])):
            try:
                parsed = urlparse(link.url)
                link.domain = parsed.netloc
                summary = fetch_page_summary(link.url)
                link.summary = summary
                link.processed = True
                if message.summary:
                    message.summary += f"\n\n\nLINK {index+1} SUMMARY: {summary}"
                else:
                    message.summary = f"LINK {index+1} SUMMARY: {summary}"
                await session.commit()
            except Exception as e:
                link.meta_data = {"error": str(e)}
                await session.commit()
        message.processed = True
        await session.commit()


