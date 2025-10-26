from functools import wraps
from adapter.cache.redis_cache import get_group_state, get_group_config, set_group_state, set_group_config, get_redis
from sqlalchemy import select
from core.di import container
from adapter.db.models import Group, BotConfig


def require_initialized_and_configured_group(func):
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        chat = update.effective_chat
        chat_id = chat.id
        state = None
        try:
            state = await get_group_state(chat_id)
        except Exception:
            state = None
        if not state:
            async with container.db() as session:
                group = await session.scalar(select(Group).where(Group.chat_id == chat_id))
                if not group:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "⚙️ This group hasn’t been initialized yet.\n"
                            "Please run /init_group to register it, then /config to set up spam detection and RAG."
                        ),
                    )
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
                state = {
                    "id": group.id,
                    "chat_id": group.chat_id,
                    "name": group.name,
                    "has_config": bool(getattr(group, "has_config", False)),
                }

        has_config = bool(state.get("has_config", False)) if isinstance(state, dict) else False
        if not has_config:
            try:
                cfg = await get_group_config(chat_id)
                has_config = bool(cfg)
            except Exception:
                has_config = False
        if not has_config:
            async with container.db() as session:
                group = await session.scalar(select(Group).where(Group.chat_id == chat_id))
                cfg = None
                if group:
                    cfg = await session.scalar(select(BotConfig).where(BotConfig.group_id == group.id))
                if not cfg:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "🧩 This group hasn’t been configured yet.\n"
                            "Please run /config to enable spam detection and RAG features."
                        ),
                    )
                    return
                try:
                    await set_group_state(chat_id, {
                        "id": group.id,
                        "chat_id": group.chat_id,
                        "name": group.name,
                        "has_config": True,
                    })
                except Exception:
                    pass
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

        return await func(update, context, *args, **kwargs)

    return wrapper


def rate_limit_per_group(max_tokens: int = 20, refill_tokens: int = 10, refill_seconds: int = 60):
    """Simple token-bucket rate limiting per group using Redis.

    max_tokens: bucket capacity per group
    refill_tokens: number of tokens added every refill_seconds
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            chat = update.effective_chat
            if not chat:
                return await func(update, context, *args, **kwargs)
            group_key = f"rate:group:{chat.id}"
            r = await get_redis()
            # Lua script to refill and consume atomically
            lua = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill = tonumber(ARGV[3])
local interval = tonumber(ARGV[4])
local tokens = tonumber(redis.call('HGET', key, 'tokens') or capacity)
local last = tonumber(redis.call('HGET', key, 'ts') or 0)
if last == 0 then last = now end
local elapsed = now - last
if elapsed >= interval then
  local add = math.floor(elapsed / interval) * refill
  tokens = math.min(capacity, tokens + add)
  last = now
end
if tokens <= 0 then
  redis.call('HSET', key, 'tokens', tokens)
  redis.call('HSET', key, 'ts', last)
  return 0
else
  tokens = tokens - 1
  redis.call('HSET', key, 'tokens', tokens)
  redis.call('HSET', key, 'ts', last)
  redis.call('EXPIRE', key, interval * 2)
  return tokens
end
"""
            import time
            remaining = await r.eval(lua, 1, group_key, int(time.time()), max_tokens, refill_tokens, refill_seconds)
            if remaining == 0:
                await context.bot.send_message(chat_id=chat.id, text="⏳ Too many messages; please slow down.")
                return
            return await func(update, context, *args, **kwargs)

        return wrapper

    return decorator


