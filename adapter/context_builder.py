from typing import Any, Dict, List, Optional
from pydantic import BaseModel
import asyncio
import logging
from datetime import datetime, timezone
import math
from core import settings
from adapter.cache.redis_cache import (
    get_recent_group_messages,
    get_recent_user_group_messages,
    get_recent_user_group_enriched,
    get_group_config,
    get_group_state,
    get_recent_user_global_meta,
    get_redis,
)
from adapter.cache.rehydrate_caches import rehydrate_group_caches
from core.di import container
from adapter.db.models import Group, BotConfig
from sqlalchemy import select

logger = logging.getLogger(__name__)

STALE_WINDOW_SECS = settings.STALE_WINDOW_SECS
MIN_CONTEXT_MSGS = settings.MIN_CONTEXT_MSGS
EMPTY_DB_COOLDOWN_SECS = settings.EMPTY_DB_COOLDOWN_SECS

class ContextBundle(BaseModel):
    group_id: int
    group_description: Optional[str]
    group_config: Optional[Dict[str, Any]]
    group_state: Optional[Dict[str, Any]]
    recent_group_messages: List[Dict[str, Any]]
    recent_user_messages: List[Dict[str, Any]]
    recent_user_enriched: List[Dict[str, Any]]
    user_global_meta: List[Dict[str, Any]]
    new_message: Dict[str, Any]
    user_frequency: Optional[Dict[str, float]] = None


def clean_timestamp(ts):
    """Return a human-readable timestamp string or 'unknown'."""
    if not ts:
        return "unknown"
    if isinstance(ts, str):
        return ts
    try:
        return str(ts)
    except Exception:
        return "unknown"


def format_recent(messages, limit: int = 5) -> str:
    """Format recent messages list into displayable lines for prompts."""
    msgs = messages[-limit:] if messages else []
    return (
        "\n".join(
            [
                f"- [{clean_timestamp(m.get('created_at'))}] {m.get('text')}"
                for m in msgs
                if m.get("text")
            ]
        )
        or "None"
    )


def format_enriched(enriched, limit: int = 3) -> str:
    """Format enriched summaries list into displayable lines for prompts."""
    if not enriched:
        return "None"
    return "\n".join(
        [
            f"- [{clean_timestamp(e.get('created_at'))}] {e.get('summary')}"
            for e in enriched[-limit:]
        ]
    )


def is_stale_cache(messages: List[Dict[str, Any]]) -> bool:
    if not messages:
        return True
    try:
        newest = max(
            datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))
            for m in messages if m.get("created_at")
        )
        age = (datetime.now(timezone.utc) - newest).total_seconds()
        return age > STALE_WINDOW_SECS
    except Exception:
        return False


async def fetch_group_config(group_id: int) -> Optional[Dict[str, Any]]:
    async with container.db() as session:
        group = await session.scalar(select(Group).where(Group.chat_id == group_id))
        if not group:
            return None
        cfg = await session.scalar(select(BotConfig).where(BotConfig.group_id == group.id))
        if not cfg:
            return None
        return {
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
        }


async def fetch_group_state(group_id: int) -> Optional[Dict[str, Any]]:
    async with container.db() as session:
        group = await session.scalar(select(Group).where(Group.chat_id == group_id))
        if not group:
            return None
        return {
            "id": group.id,
            "chat_id": group.chat_id,
            "name": group.name,
            "has_config": bool(getattr(group, "has_config", False)),
        }


def compute_frequency_score(
    messages: List[Dict[str, Any]],
    tau: float = 60.0,
) -> float:
    if not messages or len(messages) < 2:
        return 0.0
    times: List[datetime] = []
    for m in messages:
        ts = m.get("created_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            times.append(dt)
        except Exception:
            continue
    if len(times) < 2:
        return 0.0
    times.sort()
    deltas: List[float] = []
    for i in range(1, len(times)):
        delta = (times[i] - times[i - 1]).total_seconds()
        if delta > 0:
            deltas.append(delta)
    if not deltas:
        return 0.0
    avg_delta = sum(deltas) / len(deltas)
    score = math.exp(-avg_delta / tau)
    score = max(0.0, min(1.0, score))
    return float(score)


async def build_context(user_id: int, group_id: int, new_message: Dict[str, Any]) -> ContextBundle:
    """Build a ContextBundle for an incoming message.

    This function fetches recent messages and group/user metadata from Redis caches in
    parallel. If critical items like group config or state are missing from cache,
    it falls back to the database to retrieve them.

    Parameters:
    - user_id: Telegram user id
    - group_id: Telegram chat id
    - new_message: unpacked message dict to include in the bundle

    Returns:
    - ContextBundle with populated fields, suitable for downstream processing.
    """
    (
        recent_group_messages,
        recent_user_messages,
        recent_user_enriched,
        group_config,
        group_state,
        user_global_meta,
    ) = await asyncio.gather(
        get_recent_group_messages(group_id),
        get_recent_user_group_messages(user_id, group_id),
        get_recent_user_group_enriched(user_id, group_id),
        get_group_config(group_id),
        get_group_state(group_id),
        get_recent_user_global_meta(user_id),
    )

    skip_flag = None
    try:
        recent_group_msgs = recent_group_messages
        skip_key = f"group:{group_id}:rehydration_cooldown"
        r = await get_redis()
        if r is not None:
            skip_flag = await r.get(skip_key)
        if (not skip_flag) and (
            not recent_group_msgs
            or len(recent_group_msgs) < MIN_CONTEXT_MSGS
            or is_stale_cache(recent_group_msgs)
        ):
            logger.info(f"[ContextBuilder] Cache stale or thin for group {group_id}; rebuilding all caches...")
            try:
                await rehydrate_group_caches(group_id, limit=50, clear=True)
                recent_group_msgs = await get_recent_group_messages(group_id)
                if not recent_group_msgs:
                    logger.warning(f"[ContextBuilder] DB appears empty for group {group_id}; setting cooldown.")
                    if r is not None:
                        await r.setex(skip_key, EMPTY_DB_COOLDOWN_SECS, "skip")
                    return ContextBundle(
                        group_id=group_id,
                        group_description=None,
                        group_config=None,
                        group_state=None,
                        recent_group_messages=[],
                        recent_user_messages=[],
                        recent_user_enriched=[],
                        user_global_meta=[],
                        new_message=new_message,
                        user_frequency=None,
                    )
                else:
                    recent_group_messages = recent_group_msgs
            except Exception as e:
                logger.error(f"[ContextBuilder] Rehydration failed for group {group_id}: {e}")
    except Exception as e:
        logger.debug(f"[ContextBuilder] Rehydration guard error: {e}")

    if group_config is None:
        group_config = await fetch_group_config(group_id)
    if group_state is None:
        group_state = await fetch_group_state(group_id)

    group_description = (group_config or {}).get("group_description")

    group_freq = compute_frequency_score(recent_user_messages or [])
    global_freq = compute_frequency_score(user_global_meta or [])

    return ContextBundle(
        group_id=group_id,
        group_description=group_description,
        group_config=group_config,
        group_state=group_state,
        recent_group_messages=recent_group_messages or [],
        recent_user_messages=recent_user_messages or [],
        recent_user_enriched=recent_user_enriched or [],
        user_global_meta=user_global_meta or [],
        new_message=new_message,
        user_frequency={
            "within_group": group_freq,
            "across_groups": global_freq,
        },
    )



