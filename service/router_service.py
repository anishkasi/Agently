import logging
from typing import Optional

from domain.schemas.router import RouterOutput
from service.base import BaseService
from adapter.context_builder import ContextBundle  # reuse via adapter path
from core.settings import (
    ROUTER_USER_PROMPT_TEMPLATE,
    ROUTER_SYSTEM_PROMPT_V2,
)
from core.di import container
from adapter.db.models import RouterResult
from adapter.llm.client import LLMClient


class RouterService(BaseService):
    """Classify a message into intents using LLM structured output.

    This version depends on injected llm client and persists RouterResult.
    """

    def __init__(self, db=None, cache=None, llm_client=None, queue=None, logger: Optional[logging.Logger] = None) -> None:
        super().__init__(db, cache, llm_client or LLMClient(), queue, logger or logging.getLogger(__name__))

    async def classify_message(self, message: str, group_ctx: dict) -> RouterOutput | None:
        recent_messages = group_ctx.get("recent_group_messages")
        recent_user_messages = group_ctx.get("recent_user_messages")
        group_description = group_ctx.get("group_description", "")

        def _format_messages(msgs, limit: int = 6) -> str:
            if not msgs:
                return "None"
            tail = msgs[-limit:]
            lines = []
            for m in tail:
                if not isinstance(m, dict):
                    continue
                text = m.get("text")
                if not text:
                    continue
                lines.append(f"- [{m.get('created_at') or 'unknown'}] {text}")
            return "\n".join(lines) or "None"

        user_prompt = ROUTER_USER_PROMPT_TEMPLATE.format(
            group_description=group_description or "",
            recent_messages=_format_messages(recent_messages, limit=8),
            recent_user_messages=_format_messages(recent_user_messages, limit=6),
            message_text=message or "",
        )

        result = await self.llm.structured(
            prompt=user_prompt,
            model_cls=RouterOutput,
            temperature=0.0,
            system=ROUTER_SYSTEM_PROMPT_V2,
        )

        if not result:
            self.logger.warning("[RouterService] Failed to parse RouterOutput; returning None")
            return None

        # Persist
        try:
            async with container.db() as session:
                rr = RouterResult(
                    message_id=group_ctx.get("new_message", {}).get("id"),
                    intent=result.intent.value,
                    confidence=float(result.confidence),
                    is_group_qna_eligible=bool(result.is_group_qna_eligible),
                    rationale=(result.evidence.rationale if result.evidence else None),
                    cues=(result.evidence.cues if result.evidence else []),
                    recent_refs=(result.evidence.recent_refs if result.evidence else []),
                )
                session.add(rr)
                await session.commit()
        except Exception as e:
            self.logger.error(f"[RouterService] Failed to persist RouterResult: {e}")

        return result

    # Backward-compatible API used by existing handlers
    async def route(self, ctx: ContextBundle) -> RouterOutput | None:
        message_text = (ctx.new_message or {}).get("text", "")
        group_ctx = {
            "recent_group_messages": ctx.recent_group_messages,
            "recent_user_messages": ctx.recent_user_messages,
            "group_description": ctx.group_description,
            "new_message": ctx.new_message,
        }
        return await self.classify_message(message_text, group_ctx)


