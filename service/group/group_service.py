from sqlalchemy import select
from adapter.db.models import Group
from core.di import container
from adapter.cache.redis_cache import set_group_state, get_group_state


class GroupService:
    """Handles creation and retrieval of group records."""

    async def get_or_create_group(self, chat_id: int, chat_name: str):
        """
        Get or create a group record.

        Args:
            chat_id: The Telegram chat ID
            chat_name: The name of the chat

        Returns:
            The group record
        """
        cached_name = None
        try:
            cached = await get_group_state(chat_id)
            if cached and cached.get("chat_id"):
                cached_name = cached.get("name")
        except Exception:
            cached_name = None

        async with container.db() as session:
            result = await session.execute(select(Group).where(Group.chat_id == chat_id))
            group = result.scalar_one_or_none()

            if group:
                try:
                    await set_group_state(chat_id, {
                        "id": group.id,
                        "chat_id": group.chat_id,
                        "name": group.name,
                        "has_config": bool(getattr(group, "has_config", False)),
                    })
                except Exception:
                    pass
                return group

            new_group = Group(chat_id=chat_id, name=cached_name or chat_name)
            session.add(new_group)
            await session.commit()
            await session.refresh(new_group)
            try:
                await set_group_state(chat_id, {
                    "id": new_group.id,
                    "chat_id": new_group.chat_id,
                    "name": new_group.name,
                    "has_config": False,
                })
            except Exception:
                pass
            return new_group


