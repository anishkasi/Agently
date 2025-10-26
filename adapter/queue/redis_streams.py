import json
from typing import Any, Dict, Optional, Tuple

from core import settings
from adapter.cache.redis_cache import get_redis


class RedisStreamsQueue:
    """Minimal Redis Streams wrapper for enqueueing and consuming jobs."""

    def __init__(self, url: Optional[str] = None) -> None:
        self.url = url or settings.REDIS_URL

    async def enqueue(self, stream: str, payload: Dict[str, Any]) -> str:
        """Append a JSON payload to a stream and return the message id."""
        r = await get_redis(self.url)
        fields = {"payload": json.dumps(payload)}
        return await r.xadd(stream, fields)

    async def create_group(self, stream: str, group: str) -> None:
        """Ensure a consumer group exists for a stream (id set to $)."""
        r = await get_redis(self.url)
        try:
            await r.xgroup_create(stream, group, id="$", mkstream=True)
        except Exception:
            # Group may already exist
            return

    async def consume(self, stream: str, group: str, consumer: str, count: int = 10, block_ms: int = 5000) -> list[Tuple[str, Dict[str, Any]]]:
        """Read messages for a consumer from the consumer group."""
        r = await get_redis(self.url)
        resp = await r.xreadgroup(group, consumer, streams={stream: ">"}, count=count, block=block_ms)
        out: list[Tuple[str, Dict[str, Any]]] = []
        if not resp:
            return out
        for _, entries in resp:
            for msg_id, fields in entries:
                raw = fields.get("payload")
                try:
                    data = json.loads(raw) if raw else {}
                except Exception:
                    data = {}
                out.append((msg_id, data))
        return out

    async def ack(self, stream: str, group: str, msg_id: str) -> int:
        """Acknowledge a processed message id."""
        r = await get_redis(self.url)
        return await r.xack(stream, group, msg_id)


