from adapter.db.models import User, GroupUser, Group
from core.di import container
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from telegram import Update
from telegram.ext import ContextTypes
from adapter.cache.redis_cache import (
    get_recent_user_group_messages,
    get_recent_user_global_meta,
)


def map_telegram_status_to_role(status: str) -> str:
    """
    Map a Telegram status to a role.

    Args:
        status: The Telegram status

    Returns:
        The role
    """
    mapping = {
        "creator": "owner",
        "administrator": "admin",
        "member": "member",
        "restricted": "restricted",
        "left": "left",
        "kicked": "banned",
    }
    return mapping.get(status, "member")


class UserService:
    """Handles synchronization of Telegram users with the database."""

    async def _get_or_create_user(self, session, user_id: int, username: str | None, is_bot: bool = False, group_id: int | None = None):
        cache_hit = False
        try:
            if group_id is not None:
                seen = await get_recent_user_group_messages(user_id, group_id, limit=1)
                cache_hit = bool(seen)
            if not cache_hit:
                global_seen = await get_recent_user_global_meta(user_id, limit=1)
                cache_hit = bool(global_seen)
        except Exception:
            cache_hit = False

        if cache_hit:
            try:
                stmt = (
                    insert(User)
                    .values(user_id=user_id, username=username, reputation_score=100.0, is_bot=is_bot)
                    .on_conflict_do_nothing(index_elements=[User.user_id])
                )
                await session.execute(stmt)
                await session.flush()
            except Exception:
                pass
            return User(user_id=user_id, username=username, is_bot=is_bot)

        result = await session.execute(select(User).where(User.user_id == user_id))
        db_user = result.scalar_one_or_none()
        if not db_user:
            db_user = User(
                user_id=user_id,
                username=username,
                reputation_score=100.0,
                is_bot=is_bot,
            )
            session.add(db_user)
            await session.flush()
        return db_user

    async def handle_user_join_raw(self, user_id: int, username: str | None, chat_id: int, status: str, is_bot: bool = False):
        role = map_telegram_status_to_role(status)
        async with container.db() as session:
            group = await session.scalar(select(Group).where(Group.chat_id == chat_id))
            if not group:
                return None
            db_user = await self._get_or_create_user(session, user_id, username, is_bot=is_bot, group_id=chat_id)

            existing_link = await session.scalar(
                select(GroupUser).where(
                    GroupUser.group_id == group.chat_id,
                    GroupUser.user_id == db_user.user_id,
                )
            )
            if existing_link:
                existing_link.role = role
                existing_link.is_active = True
            else:
                stmt = (
                    insert(GroupUser)
                    .values(group_id=group.chat_id, user_id=db_user.user_id, role=role, is_active=True)
                    .on_conflict_do_nothing()
                )
                await session.execute(stmt)

            await session.commit()
            return db_user

    async def handle_user_leave_raw(self, user_id: int, chat_id: int, action: str = "left"):
        role_value = "banned" if action == "banned" else "left"
        async with container.db() as session:
            group = await session.scalar(select(Group).where(Group.chat_id == chat_id))
            if not group:
                return
            user = await session.scalar(select(User).where(User.user_id == user_id))
            if not user:
                return
            await session.execute(
                update(GroupUser)
                .where(GroupUser.group_id == group.chat_id, GroupUser.user_id == user.user_id)
                .values(role=role_value, is_active=False)
            )
            await session.commit()

    async def handle_role_update_raw(self, user_id: int, chat_id: int, new_role: str):
        async with container.db() as session:
            group = await session.scalar(select(Group).where(Group.chat_id == chat_id))
            if not group:
                return
            user = await session.scalar(select(User).where(User.user_id == user_id))
            if not user:
                return
            await session.execute(
                update(GroupUser)
                .where(GroupUser.group_id == group.chat_id, GroupUser.user_id == user.user_id)
                .values(role=new_role, is_active=True)
            )
            await session.commit()

    async def sync_all_members(self, context, chat_id: int, group_id: int):
        async with container.db() as session:
            admins = await context.bot.get_chat_administrators(chat_id)
            for admin in admins:
                tg_user = admin.user
                role = map_telegram_status_to_role(admin.status)
                db_user = await self._get_or_create_user(session, tg_user.id, tg_user.username, is_bot=tg_user.is_bot, group_id=chat_id)
                stmt = (
                    insert(GroupUser)
                    .values(group_id=chat_id, user_id=db_user.user_id, role=role, is_active=True)
                    .on_conflict_do_update(
                        index_elements=[GroupUser.group_id, GroupUser.user_id],
                        set_={"role": role, "is_active": True},
                    )
                )
                await session.execute(stmt)
            await session.commit()

    async def handle_user_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tg = update.chat_member.new_chat_member
        await self.handle_user_join_raw(
            user_id=tg.user.id,
            username=tg.user.username,
            chat_id=update.chat.id,
            status=tg.status,
            is_bot=tg.user.is_bot,
        )

    async def handle_user_leave(self, update: Update, context: ContextTypes.DEFAULT_TYPE, action: str = "left"):
        user_id = None
        if getattr(update.chat_member, "left_chat_member", None):
            user_id = update.chat_member.left_chat_member.user.id
        elif getattr(update.chat_member, "old_chat_member", None):
            user_id = update.chat_member.old_chat_member.user.id
        if user_id is None:
            return
        await self.handle_user_leave_raw(user_id=user_id, chat_id=update.chat.id, action=action)

    async def handle_role_update(self, tg_user, chat_id: int, new_role: str):
        await self.handle_role_update_raw(user_id=tg_user.id, chat_id=chat_id, new_role=new_role)


