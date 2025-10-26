"""Redis cache module for MyAgent.

Provides caching utilities for:
- User message history (per-group and global)
- Group state and configuration
- Recent group messages
- Enriched media messages
- Task status tracking
- Cache rehydration from database
"""

from adapter.cache.redis_cache import (
    get_redis,
    # User-group caches
    append_user_group_message,
    get_recent_user_group_messages,
    # User global caches
    append_user_global_meta,
    get_recent_user_global_meta,
    # Group state caches
    set_group_state,
    get_group_state,
    # Group config caches
    set_group_config,
    get_group_config,
    # Group message caches
    append_group_message,
    get_recent_group_messages,
    # Task status caches
    set_task_status,
    get_task_status,
    # Enriched message caches
    append_user_group_enriched,
    get_recent_user_group_enriched,
)

from adapter.cache.rehydrate_caches import (
    rehydrate_group_caches,
    rehydrate_all_caches,
)

__all__ = [
    # Core
    "get_redis",
    # User-group caches
    "append_user_group_message",
    "get_recent_user_group_messages",
    # User global caches
    "append_user_global_meta",
    "get_recent_user_global_meta",
    # Group state caches
    "set_group_state",
    "get_group_state",
    # Group config caches
    "set_group_config",
    "get_group_config",
    # Group message caches
    "append_group_message",
    "get_recent_group_messages",
    # Task status caches
    "set_task_status",
    "get_task_status",
    # Enriched message caches
    "append_user_group_enriched",
    "get_recent_user_group_enriched",
    # Rehydration utilities
    "rehydrate_group_caches",
    "rehydrate_all_caches",
]
