import os
import logging
import json
from typing import Any, Dict, Optional, Type

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from core import settings


logger = logging.getLogger(__name__)


_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        _client = AsyncOpenAI(api_key=api_key)
    return _client


def _truncate(s: str, limit: int = 800) -> str:
    if not s:
        return s
    return s[:limit] + ("..." if len(s) > limit else "")


class LLMClient:
    """Thin async OpenAI client wrapper for classify/structured operations."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.OPENAI_MODEL

    async def classify(self, prompt: str) -> Dict[str, Any]:
        client = _get_client()
        try:
            logger.info(f"LLM classify prompt: {_truncate(prompt)}")
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You output only JSON objects."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=256,
            )
            content = (resp.choices[0].message.content or "").strip()
            logger.info(f"LLM classify output: {_truncate(content)}")
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    cats = data.get("categories", [])
                    if not isinstance(cats, list):
                        cats = []
                    return {
                        "spam": bool(data.get("spam", False)),
                        "confidence": float(data.get("confidence", 0.5)),
                        "reason": str(data.get("reason", "")),
                        "categories": cats,
                    }
            except Exception:
                pass
            lowered = content.lower()
            is_spam = "spam" in lowered and "not" not in lowered
            conf = 0.7 if is_spam else 0.4
            return {"spam": is_spam, "confidence": conf, "reason": content, "categories": []}
        except Exception as e:
            logger.error(f"LLM classify failed: {e}")
            return {"spam": False, "confidence": 0.5, "reason": "LLM error"}

    async def structured(
        self,
        prompt: str,
        model_cls: Type[BaseModel],
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
        system: Optional[str] = None,
        extra_instructions: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[BaseModel]:
        """Call an LLM with a structured output prompt and return a Pydantic model instance.

        Args:
            prompt: The user prompt to send to the LLM
            model_cls: The Pydantic model class to validate the output against
            temperature: The temperature to use for the LLM call
            max_tokens: The maximum number of tokens to generate
            system: Optional system prompt to prepend to the user prompt
            extra_instructions: Additional instructions to add to the system prompt
            **kwargs: Additional keyword arguments to pass to the LLM client

        Returns:
            The Pydantic model instance if successful, None if there was an error
        """
        client = _get_client()
        try:
            schema = model_cls.model_json_schema()
            schema_text = json.dumps(schema)
            sys_msg = system or (
                "You are a careful assistant. Output only a single JSON object that strictly "
                "validates against the provided JSON Schema. Do not include commentary."
            )
            if extra_instructions:
                sys_msg = f"{sys_msg}\n\nAdditional constraints:\n{extra_instructions}"
            messages = [
                {"role": "system", "content": sys_msg},
                {
                    "role": "user",
                    "content": (
                        "JSON Schema (Draft) for your output follows. Respond with one JSON object matching it.\n"
                        f"SCHEMA:\n{schema_text}\n\n"
                        f"PROMPT:\n{prompt}"
                    ),
                },
            ]
            logger.info(f"LLM structured prompt: { _truncate(prompt) }")
            resp = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                **kwargs,
            )
            content = (resp.choices[0].message.content or "").strip()
            logger.info(f"LLM structured output: { _truncate(content) }")
            data = json.loads(content)
            try:
                return model_cls.model_validate(data)
            except ValidationError as ve:
                logger.error(f"Structured validation failed: {ve}")
                return None
        except Exception as e:
            logger.error(f"LLM structured failed: {e}")
            return None


