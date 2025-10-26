from typing import Any, Dict, Iterable
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from core.di import container
from adapter.db.models import Group, BotConfig, Message, MediaAsset
from adapter.cache.redis_cache import (
    set_group_state,
    set_group_config,
    append_group_message,
    append_user_group_message,
    append_user_global_meta,
    append_user_group_enriched,
    get_redis,
)

"""Utilities to rehydrate Redis caches from the database.
This module repopulates cache layers used by the Context Builder so that
context retrieval remains complete even after cache evictions or inactivity.
"""

async def _rehydrate_group_state_and_config(session, chat_id: int) -> None:
    group = await session.scalar(select(Group).where(Group.chat_id == chat_id))
    if not group:
        return
    try:
        await set_group_state(chat_id, {
            "id": group.id,
            "chat_id": group.chat_id,
            "name": group.name,
            "has_config": bool(getattr(group, "has_config", False)),
        })
    except Exception:
        pass
    cfg = await session.scalar(select(BotConfig).where(BotConfig.group_id == group.id))
    if cfg:
        try:
            await set_group_config(chat_id, {
                "id": cfg.id,
                "group_id": cfg.group_id,
                "group_description": getattr(cfg, "group_description", ""),
                "spam_sensitivity": cfg.spam_sensitivity,
                "spam_confidence_threshold": cfg.spam_confidence_threshold,
                "spam_rules": cfg.spam_rules,
                "rag_enabled": cfg.rag_enabled,
                "personality": cfg.personality,
                "moderation_features": cfg.moderation_features,
                "tools_enabled": cfg.tools_enabled,
                "last_updated": getattr(cfg, "last_updated", None).isoformat() if getattr(cfg, "last_updated", None) else None,
            })
        except Exception:
            pass


async def _rehydrate_messages_for_group(session, chat_id: int, *, limit: int = 200) -> None:
    rows: Iterable[Message] = (
        await session.execute(
            select(Message)
            .options(selectinload(Message.media_assets))
            .where(Message.group_id == chat_id)
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
    ).scalars().all()
    for m in reversed(rows):
        payload: Dict[str, Any] = {
            "id": m.id,
            "type": m.message_type,
            "text": m.content,
            "user_id": m.user_id,
            "group_id": m.group_id,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        try:
            await append_group_message(chat_id, payload, limit=limit)
        except Exception:
            pass
        try:
            await append_user_group_message(m.user_id, chat_id, payload, limit=limit)
        except Exception:
            pass
        try:
            await append_user_global_meta(m.user_id, payload, limit=limit)
        except Exception:
            pass
        if m.message_type in {"image", "audio", "video", "document", "GIF"}:
            try:
                media_asset = await session.scalar(select(MediaAsset).where(MediaAsset.message_id == m.id))
                await append_user_group_enriched(
                    m.user_id,
                    chat_id,
                    m.id,
                    summary=getattr(media_asset, "summary", None),
                    created_at=m.created_at.isoformat() if m.created_at else None,
                )
            except Exception:
                pass


async def rehydrate_group_caches(group_chat_id: int, *, limit: int = 200, clear: bool = True) -> None:
    r = await get_redis()
    if clear and r is not None:
        try:
            await r.delete(f"group:{group_chat_id}:state")
            await r.delete(f"group:{group_chat_id}:config")
            await r.delete(f"group:{group_chat_id}:recent_msgs")
        except Exception:
            pass
        try:
            cursor = 0
            pattern_ug = f"user:*:group:{group_chat_id}"
            pattern_uge = f"user:*:group:{group_chat_id}:enriched_recent"
            while True:
                cursor, keys = await r.scan(cursor=cursor, match=pattern_ug, count=500)
                if keys:
                    await r.delete(*keys)
                if cursor == 0:
                    break
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor=cursor, match=pattern_uge, count=500)
                if keys:
                    await r.delete(*keys)
                if cursor == 0:
                    break
        except Exception:
            pass

    async with container.db() as session:
        await _rehydrate_group_state_and_config(session, group_chat_id)
        await _rehydrate_messages_for_group(session, group_chat_id, limit=limit)


async def rehydrate_all_caches(*, limit: int = 200, clear: bool = True, flush_all: bool = False) -> None:
    r = await get_redis()
    if flush_all and r is not None:
        try:
            await r.flushdb()
        except Exception:
            pass

    async with container.db() as session:
        chat_ids = (
            await session.execute(select(Group.chat_id))
        ).scalars().all()

    for chat_id in chat_ids:
        await rehydrate_group_caches(chat_id, limit=limit, clear=clear)


