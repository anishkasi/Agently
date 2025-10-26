import json
from typing import Any, Dict, List, Optional

from redis import asyncio as redis_async

from core import settings

"""Async Redis cache utilities for moderation/routing.

Caches:
- UserGroupCache (user:{user_id}:group:{group_id}) → last N user messages in a group
- UserGroupEnrichedCache (user:{user_id}:group:{group_id}:enriched_recent) → last N enriched messages in a group
- UserGlobalCache (user:{user_id}:global) → recent message metadata across groups
- GroupStateCache (group:{group_id}:state) → group state snapshot {id, chat_id, name, has_config}
- GroupConfigCache (group:{group_id}:config) → group config snapshot (BotConfig fields)
- GroupMessageCache (group:{group_id}:recent_msgs) → last X group messages
- TaskCache (message:{message_id}:status) → async processing state

Usage:
  await get_redis()
  await append_user_group_message(1, 100, {"text": "hi"})
  msgs = await get_recent_user_group_messages(1, 100)
"""


_redis: Optional[redis_async.Redis] = None


async def get_redis(url: Optional[str] = None) -> redis_async.Redis:
    global _redis
    if _redis is None:
        _redis = redis_async.from_url(
            url or settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis


def _key_user_group(user_id: int, group_id: int) -> str:
    return f"user:{user_id}:group:{group_id}"


async def append_user_group_message(user_id: int, group_id: int, message: Dict[str, Any], *, ttl: int = settings.USER_CACHE_TTL, limit: int = settings.USER_CACHE_LIMIT) -> None:
    r = await get_redis()
    key = _key_user_group(user_id, group_id)
    await r.rpush(key, json.dumps(message))
    await r.ltrim(key, -limit, -1)
    if ttl > 0:
        await r.expire(key, ttl)


async def get_recent_user_group_messages(user_id: int, group_id: int, *, limit: int = settings.USER_CACHE_LIMIT) -> List[Dict[str, Any]]:
    r = await get_redis()
    key = _key_user_group(user_id, group_id)
    items = await r.lrange(key, -limit, -1)
    parsed = [json.loads(i) for i in items]
    seen = set()
    out_rev: List[Dict[str, Any]] = []
    for m in reversed(parsed):
        mid = (m or {}).get("id")
        if mid is None or mid in seen:
            continue
        seen.add(mid)
        out_rev.append(m)
    return list(reversed(out_rev))


def _key_user_global(user_id: int) -> str:
    return f"user:{user_id}:global"


async def append_user_global_meta(user_id: int, meta: Dict[str, Any], *, ttl: int = settings.USER_GLOBAL_TTL, limit: int = settings.USER_CACHE_LIMIT) -> None:
    r = await get_redis()
    key = _key_user_global(user_id)
    await r.rpush(key, json.dumps(meta))
    await r.ltrim(key, -limit, -1)
    if ttl > 0:
        await r.expire(key, ttl)


async def get_recent_user_global_meta(user_id: int, *, limit: int = settings.USER_CACHE_LIMIT) -> List[Dict[str, Any]]:
    r = await get_redis()
    key = _key_user_global(user_id)
    items = await r.lrange(key, -limit, -1)
    parsed = [json.loads(i) for i in items]
    seen = set()
    out_rev: List[Dict[str, Any]] = []
    for m in reversed(parsed):
        mid = (m or {}).get("id")
        if mid is None or mid in seen:
            continue
        seen.add(mid)
        out_rev.append(m)
    return list(reversed(out_rev))


def _key_group_state(group_id: int) -> str:
    return f"group:{group_id}:state"


async def set_group_state(group_id: int, state: Dict[str, Any], *, ttl: int = settings.GROUP_STATE_TTL) -> None:
    r = await get_redis()
    key = _key_group_state(group_id)
    await r.set(key, json.dumps(state), ex=ttl if ttl > 0 else None)


async def get_group_state(group_id: int) -> Optional[Dict[str, Any]]:
    r = await get_redis()
    key = _key_group_state(group_id)
    raw = await r.get(key)
    return json.loads(raw) if raw else None


def _key_group_config(group_id: int) -> str:
    return f"group:{group_id}:config"


async def set_group_config(group_id: int, config: Dict[str, Any], *, ttl: int = settings.GROUP_CONFIG_TTL) -> None:
    r = await get_redis()
    key = _key_group_config(group_id)
    await r.set(key, json.dumps(config), ex=ttl if ttl > 0 else None)


async def get_group_config(group_id: int) -> Optional[Dict[str, Any]]:
    r = await get_redis()
    key = _key_group_config(group_id)
    raw = await r.get(key)
    return json.loads(raw) if raw else None


def _key_group_msgs(group_id: int) -> str:
    return f"group:{group_id}:recent_msgs"


async def append_group_message(group_id: int, message: Dict[str, Any], *, ttl: int = settings.GROUP_MSG_TTL, limit: int = settings.GROUP_MSG_LIMIT) -> None:
    r = await get_redis()
    key = _key_group_msgs(group_id)
    await r.rpush(key, json.dumps(message))
    await r.ltrim(key, -limit, -1)
    if ttl > 0:
        await r.expire(key, ttl)


async def get_recent_group_messages(group_id: int, *, limit: int = settings.GROUP_MSG_LIMIT) -> List[Dict[str, Any]]:
    r = await get_redis()
    key = _key_group_msgs(group_id)
    items = await r.lrange(key, -limit, -1)
    parsed = [json.loads(i) for i in items]
    seen = set()
    out_rev: List[Dict[str, Any]] = []
    for m in reversed(parsed):
        mid = (m or {}).get("id")
        if mid is None or mid in seen:
            continue
        seen.add(mid)
        out_rev.append(m)
    return list(reversed(out_rev))


def _key_task_status(message_id: int) -> str:
    return f"message:{message_id}:status"


async def set_task_status(message_id: int, status: str, *, ttl: int = settings.TASK_TTL) -> None:
    r = await get_redis()
    key = _key_task_status(message_id)
    await r.set(key, status, ex=ttl if ttl > 0 else None)


async def get_task_status(message_id: int) -> Optional[str]:
    r = await get_redis()
    key = _key_task_status(message_id)
    return await r.get(key)


def _key_user_group_enriched(user_id: int, group_id: int) -> str:
    return f"user:{user_id}:group:{group_id}:enriched_recent"


async def append_user_group_enriched(user_id: int, group_id: int, message_id: int, summary: str, created_at: Optional[str] = None, *, ttl: int = settings.USER_GLOBAL_TTL, limit: int = settings.USER_ENRICH_LIMIT) -> None:
    r = await get_redis()
    key = _key_user_group_enriched(user_id, group_id)
    item = {"id": message_id, "summary": summary, "created_at": created_at}
    await r.rpush(key, json.dumps(item))
    await r.ltrim(key, -limit, -1)
    if ttl > 0:
        await r.expire(key, ttl)


async def get_recent_user_group_enriched(user_id: int, group_id: int, *, limit: int = settings.USER_ENRICH_LIMIT) -> List[Dict[str, Any]]:
    r = await get_redis()
    key = _key_user_group_enriched(user_id, group_id)
    items = await r.lrange(key, -limit, -1)
    parsed = [json.loads(i) for i in items]
    seen = set()
    out_rev: List[Dict[str, Any]] = []
    for m in reversed(parsed):
        mid = (m or {}).get("id")
        if mid is None or mid in seen:
            continue
        seen.add(mid)
        out_rev.append(m)
    return list(reversed(out_rev))


