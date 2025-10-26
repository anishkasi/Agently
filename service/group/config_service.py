from adapter.db.models import BotConfig, Group
from core.di import container
from sqlalchemy import select, update

from service.group.group_service import GroupService
from adapter.cache.redis_cache import set_group_config, set_group_state


class ConfigService:
    """Manages bot configuration while relying on GroupService for group initialization."""

    def __init__(self):
        self.group_service = GroupService()

    async def get_group_config(self, chat_id: int, chat_name: str | None = None):
        """Fetch existing BotConfig for a given chat_id. Does NOT create a default config."""
        async with container.db() as session:
            group = await self.group_service.get_or_create_group(chat_id, chat_name or f"Group-{chat_id}")
            cfg = await session.scalar(select(BotConfig).where(BotConfig.group_id == group.id))
            if cfg:
                # Populate group config cache keyed by chat_id
                try:
                    await set_group_config(group.chat_id, {
                        "id": cfg.id,
                        "group_id": cfg.group_id,
                        "group_description": cfg.group_description,
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
            return cfg

    async def create_group_config(self, chat_id: int, chat_name: str | None, data: dict):
        """
        Create a new BotConfig for a group.

        Args:
            chat_id: The Telegram chat ID
            chat_name: The name of the chat
            data: The data to create the config with

        Returns:
            The new BotConfig
        """
        async with container.db() as session:
            group = await self.group_service.get_or_create_group(chat_id, chat_name or f"Group-{chat_id}")
            # If already exists, no-op return existing
            existing = await session.scalar(select(BotConfig).where(BotConfig.group_id == group.id))
            if existing:
                return existing
            new_cfg = BotConfig(
                group_id=group.id,
                group_description=str(data.get("group_description", "")),
                spam_sensitivity=str(data.get("spam_sensitivity", "medium")),
                spam_confidence_threshold=float(data.get("spam_confidence_threshold", 0.7)),
                spam_rules=str(data.get("spam_rules", "")),
                rag_enabled=bool(data.get("rag_enabled", True)),
                personality=str(data.get("personality", "neutral")),
                moderation_features=dict(data.get("moderation_features", {
                    "spam_detection": True,
                    "harmful_intent": False,
                    "fud_filtering": True,
                    "nsfw_detection": False,
                })),
                tools_enabled=dict(data.get("tools_enabled", {})),
            )
            session.add(new_cfg)
            await session.commit()
            await session.refresh(new_cfg)
            # Mark group as configured and refresh caches
            try:
                await session.execute(
                    update(Group).where(Group.id == group.id).values(has_config=True)
                )
                await session.commit()
            except Exception:
                pass
            try:
                await set_group_config(group.chat_id, {
                    "id": new_cfg.id,
                    "group_id": new_cfg.group_id,
                    "group_description": new_cfg.group_description,
                    "spam_sensitivity": new_cfg.spam_sensitivity,
                    "spam_confidence_threshold": new_cfg.spam_confidence_threshold,
                    "spam_rules": new_cfg.spam_rules,
                    "rag_enabled": new_cfg.rag_enabled,
                    "personality": new_cfg.personality,
                    "moderation_features": new_cfg.moderation_features,
                    "tools_enabled": new_cfg.tools_enabled,
                    "last_updated": getattr(new_cfg, "last_updated", None).isoformat() if getattr(new_cfg, "last_updated", None) else None,
                })
                # Update group state cache has_config immediately
                await set_group_state(chat_id, {
                    "id": group.id,
                    "chat_id": group.chat_id,
                    "name": group.name,
                    "has_config": True,
                })
            except Exception:
                pass
            return new_cfg

    async def update_config_field(self, config_id: int, field: str, value):
        """Update a single config field by config ID."""
        async with container.db() as session:
            await session.execute(
                update(BotConfig)
                .where(BotConfig.id == config_id)
                .values({field: value})
            )
            await session.commit()

            # Refresh cache for this group's config after update
            try:
                cfg = await session.scalar(select(BotConfig).where(BotConfig.id == config_id))
                if cfg:
                    # Resolve chat_id via Group relation
                    group = await session.scalar(select(Group).where(Group.id == cfg.group_id))
                    if group:
                        await set_group_config(group.chat_id, {
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

    async def update_config_field_by_chat_id(self, chat_id: int, field: str, value, chat_name: str = None):
        """Update config field by chat_id. Does NOT create a config if missing."""
        cfg = await self.get_group_config(chat_id, chat_name)
        if not cfg:
            return
        await self.update_config_field(cfg.id, field, value)


