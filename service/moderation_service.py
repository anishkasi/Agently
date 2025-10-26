from typing import Any, Optional, List
import logging
import math
from datetime import datetime, timedelta

from telegram import Bot
from sqlalchemy import select

from adapter.context_builder import ContextBundle, build_context
from adapter.context_builder import format_recent, format_enriched
from adapter.llm.client import LLMClient as LLMService
from adapter.cache.redis_cache import get_redis
from adapter.db.models import User, SpamResult
from core.di import container
from domain.schemas.moderation import SpamVerdict

from core.settings import (
    MOD_SYSTEM_PROMPT as SYSTEM_PROMPT,
    MOD_DECISION_RULE_PROMPT as DECISION_RULE_PROMPT,
    MOD_DECISION_LOGIC_PROMPT as DECISION_LOGIC_PROMPT,
    MOD_EXAMPLE_PROMPT as EXAMPLE_PROMPT,
    DEFAULT_START_SCORE,
    WARNING_THRESHOLD,
    STRONG_WARNING_THRESHOLD,
    PROBATION_THRESHOLD,
    BAN_THRESHOLD,
    DAILY_RECOVERY_POINTS,
    MAX_SCORE,
)


def build_spam_prompt(ctx) -> str:
    """
    Build a detailed spam detection prompt by injecting context variables from ContextBundle.
    """

    description = ctx.group_description or "No group description available."
    config = getattr(ctx, "group_config", None) or {}
    
    # config is a dict, use .get() not getattr()
    tone = config.get("personality", "neutral") if isinstance(config, dict) else "neutral"
    rules = config.get("spam_rules", "") if isinstance(config, dict) else ""
    rules = rules or "No explicit spam rules provided."
    sensitivity = config.get("spam_sensitivity", "medium") if isinstance(config, dict) else "medium"
    threshold = config.get("spam_confidence_threshold", 0.7) if isinstance(config, dict) else 0.7

    recent_group_msgs = format_recent(ctx.recent_group_messages, limit=5)
    recent_user_msgs = format_recent(ctx.recent_user_messages, limit=5)
    enriched_msgs = format_enriched(ctx.recent_user_enriched, limit=3)

    freq = ctx.user_frequency or {}
    within_score = freq.get("within_group", 0.0)
    across_score = freq.get("across_groups", 0.0)
    new_message = ctx.new_message.get("text", "")

    return f"""
{SYSTEM_PROMPT}

Group Description:
{description}

Group Config:
- Personality (Tone): {tone}
- Spam Sensitivity: {sensitivity}
- Confidence Threshold: {threshold}

Spam Rules:
{rules}

Recent Group Messages (most recent first):
{recent_group_msgs}

Recent User Messages in this group:
{recent_user_msgs}

Recent Enriched Summaries:
{enriched_msgs}

User Behavioral Scores:
- within_group_frequency_score: {within_score:.4f}
- across_groups_frequency_score: {across_score:.4f}

New Message to Evaluate:
{new_message}

{DECISION_RULE_PROMPT}

{DECISION_LOGIC_PROMPT}

{EXAMPLE_PROMPT}
"""


class SpamDetector:
    """Spam detection and treatment pipeline.

    Responsibilities:
    - Build spam prompt from ContextBundle
    - Call LLM to classify spam/not-spam
    - Apply reputation changes and take actions (warn, probation, ban)
    - Persist results and side-effects
    """

    def __init__(self) -> None:
        self.llm = LLMService()

    async def analyze(self, ctx: ContextBundle) -> SpamVerdict:
        """Analyze a ContextBundle and return a spam verdict.

        Heuristic component:
          - If frequency is high (>= 0.8) within group or globally, bias toward spam.

        LLM component:
          - Ask the model to rate if the message is irrelevant/unsolicited for the group
            context and description.
        """
        prompt = build_spam_prompt(ctx)

        # Call LLM service (assumed async method)
        llm_result: dict[str, Any]
        try:
            llm_result = await self.llm.classify(prompt)
        except Exception:
            llm_result = {"spam": False, "confidence": 0.5, "reason": "LLM unavailable", "categories": []}

        categories = llm_result.get("categories", [])
        if not isinstance(categories, list):
            categories = []

        return SpamVerdict(
            spam=bool(llm_result.get("spam", False)),
            confidence=float(llm_result.get("confidence", 0.5)),
            reason=llm_result.get("reason", ""),
            categories=categories,
        )

    async def get_reputation(self, user_id: int, group_id: int) -> int:
        key = f"user:{user_id}:group:{group_id}:reputation"
        r = await get_redis()
        score = await r.get(key)
        if score is None:
            try:
                async with container.db() as session:
                    user_row = await session.scalar(select(User).where(User.user_id == user_id))
                    score = int(getattr(user_row, "reputation_score", DEFAULT_START_SCORE) or DEFAULT_START_SCORE)
            except Exception:
                score = DEFAULT_START_SCORE
            await r.set(key, score)
            return score
        return int(score)

    async def set_reputation(self, user_id: int, group_id: int, score: int) -> None:
        key = f"user:{user_id}:group:{group_id}:reputation"
        r = await get_redis()
        await r.set(key, score)
        try:
            async with container.db() as session:
                user_row = await session.scalar(select(User).where(User.user_id == user_id))
                if user_row:
                    user_row.reputation_score = float(score)
                    await session.commit()
        except Exception as e:
            logging.getLogger(__name__).error(f"[SpamTreatment] Failed to persist reputation to DB for user {user_id}: {e}")

    @staticmethod
    def compute_penalty(verdict: SpamVerdict) -> int:
        cats = getattr(verdict, "categories", None) or []
        category = cats[0] if cats else None
        base = {"promo": 5, "off-topic": 5, "link-flood": 10, "harmful": 30, "scam": 30, "nsfw": 30}.get(category, 5)
        scaled = math.ceil(base * max(float(getattr(verdict, "confidence", 0.5)), 0.5))
        return scaled or 3

    async def treat_spam(self, verdict: SpamVerdict, ctx: ContextBundle, bot: Bot) -> None:
        user_id = (ctx.new_message or {}).get("user_id")
        if not user_id and ctx.recent_user_messages:
            last_um = ctx.recent_user_messages[-1] or {}
            user_id = last_um.get("user_id")
        if not user_id and ctx.user_global_meta:
            last_glob = ctx.user_global_meta[-1] or {}
            user_id = last_glob.get("user_id")
        group_id = ctx.group_id

        if not user_id or not group_id:
            logging.getLogger(__name__).warning("[SpamTreatment] Missing user_id or group_id in context.")
            return

        score = await self.get_reputation(user_id, group_id)

        if not bool(getattr(verdict, "spam", False)):
            logging.getLogger(__name__).info(f"[SpamTreatment] User {user_id} in group {group_id}: not spam, no action needed.")
            async with container.db() as session:
                sr = SpamResult(
                    message_id=(ctx.new_message or {}).get("id"),
                    spam=False,
                    confidence=float(getattr(verdict, "confidence", 0.0)),
                    category=(getattr(verdict, "categories", [""]) or [""])[0],
                    reason=str(getattr(verdict, "reason", "")),
                    treatment_action="none",
                    treatment_message=None,
                )
                session.add(sr)
                await session.commit()
            return

        penalty = self.compute_penalty(verdict)
        new_score = max(score - penalty, 0)
        await self.set_reputation(user_id, group_id, new_score)

        action = "none"
        action_msg = None
        if new_score <= BAN_THRESHOLD:
            action = "ban"
            action_msg = f"❌ User {user_id} banned (reputation {new_score}/100)."
            await self.handle_ban(user_id, group_id, new_score, ctx, bot)
        elif new_score <= PROBATION_THRESHOLD:
            action = "probation"
            action_msg = f"🚨 You’re on probation (score {new_score}/100). Continued spam will result in removal."
            await self.handle_probation(user_id, group_id, new_score, ctx, bot)
        elif new_score <= STRONG_WARNING_THRESHOLD:
            action = "warning_strong"
            action_msg = f"⚠️ Your messages are frequently flagged as spam. Current reputation: {new_score}/100. Further violations may lead to removal."
            await self.send_warning(user_id, group_id, "strong", new_score, ctx, bot)
        elif new_score <= WARNING_THRESHOLD:
            action = "warning_mild"
            action_msg = f"⚠️ Heads up! Some of your recent messages may be spam. Your reputation score is {new_score}/100."
            await self.send_warning(user_id, group_id, "mild", new_score, ctx, bot)

        deleted_flag = await self.delete_message_if_needed(ctx, verdict, bot)

        try:
            async with container.db() as session:
                cats = getattr(verdict, "categories", None) or []
                category_str = cats[0] if len(cats) > 0 else ""
                sr = SpamResult(
                    message_id=(ctx.new_message or {}).get("id"),
                    spam=bool(getattr(verdict, "spam", False)),
                    confidence=float(getattr(verdict, "confidence", 0.0)),
                    category=category_str,
                    reason=str(getattr(verdict, "reason", "")),
                    treatment_action=action,
                    treatment_message=action_msg,
                    deleted=bool(deleted_flag),
                    points_docked=int(penalty),
                    final_reputation=int(new_score),
                )
                session.add(sr)
                await session.commit()
        except Exception as e:
            logging.getLogger(__name__).error(f"[SpamTreatment] Failed to persist SpamResult outcome: {e}")

    async def delete_message_if_needed(self, ctx: ContextBundle, verdict: SpamVerdict, bot: Bot) -> bool:
        try:
            features = {}
            if isinstance(ctx.group_config, dict):
                features = ctx.group_config.get("moderation_features", {}) or {}
            threshold = 0.7
            if isinstance(ctx.group_config, dict):
                threshold = float(ctx.group_config.get("spam_confidence_threshold", threshold))
            if features.get("spam_detection", True) and bool(getattr(verdict, "spam", False)) and float(getattr(verdict, "confidence", 0.0)) >= threshold:
                # Get Telegram message ID (not DB ID)
                new_msg = ctx.new_message or {}
                telegram_msg_id = new_msg.get("telegram_message_id")
                
                # Only use telegram_message_id if it exists and is not None
                if telegram_msg_id is not None:
                    try:
                        await bot.delete_message(chat_id=ctx.group_id, message_id=telegram_msg_id)
                        logging.getLogger(__name__).info(f"[SpamTreatment] Deleted spam message {telegram_msg_id} in group {ctx.group_id}")
                        return True
                    except Exception as del_err:
                        logging.getLogger(__name__).error(f"[SpamTreatment] Delete failed for message {telegram_msg_id}: {del_err}")
                else:
                    logging.getLogger(__name__).warning(f"[SpamTreatment] No telegram_message_id found in context, cannot delete message")
            return False
        except Exception as e:
            logging.getLogger(__name__).error(f"[SpamTreatment] Failed to delete message: {e}")
            return False

    async def send_warning(self, user_id: int, group_id: int, level: str, score: int, ctx: ContextBundle, bot: Bot) -> None:
        try:
            if level == "strong":
                msg = f"⚠️ Your messages are frequently flagged as spam. Current reputation: {score}/100.\nFurther violations may lead to removal."
            else:
                msg = f"⚠️ Heads up! Some of your recent messages may be spam. Your reputation score is {score}/100."
            await bot.send_message(chat_id=group_id, text=msg)
        except Exception as e:
            logging.getLogger(__name__).error(f"[SpamTreatment] Failed to send warning: {e}")

    async def handle_probation(self, user_id: int, group_id: int, score: int, ctx: ContextBundle, bot: Bot) -> None:
        try:
            msg = f"🚨 You’re on probation (score {score}/100). Continued spam will result in removal."
            await bot.send_message(chat_id=group_id, text=msg)
        except Exception as e:
            logging.getLogger(__name__).error(f"[SpamTreatment] Failed to send probation notice: {e}")

    async def handle_ban(self, user_id: int, group_id: int, score: int, ctx: ContextBundle, bot: Bot) -> None:
        try:
            await bot.send_message(chat_id=group_id, text=f"❌ User {user_id} banned (reputation {score}/100).")
            # Optionally kick: await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
        except Exception as e:
            logging.getLogger(__name__).error(f"[SpamTreatment] Failed to ban/notify: {e}")


async def detect_and_treat_spam(user_id: int, group_id: int, new_message: dict[str, Any], bot: Bot, ctx: Optional[ContextBundle] = None) -> SpamVerdict:
    """Entry point to produce a spam verdict and apply treatments."""
    if ctx is None:
        ctx = await build_context(user_id=user_id, group_id=group_id, new_message=new_message)
    detector = SpamDetector()
    verdict = await detector.analyze(ctx)
    await detector.treat_spam(verdict, ctx, bot)
    return verdict
