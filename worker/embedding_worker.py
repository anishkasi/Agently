import asyncio
import logging
from core import settings
from adapter.queue.redis_streams import RedisStreamsQueue
from adapter.llm.client import _get_client
from adapter.cache.redis_cache import get_redis


logger = logging.getLogger(__name__)


async def run_embedding_worker():
    queue = RedisStreamsQueue(settings.REDIS_URL)
    await queue.create_group(settings.QUEUE_STREAM_EMBEDDINGS, settings.QUEUE_GROUP_EMBEDDINGS)
    consumer_name = "embedder-1"
    client = _get_client()
    r = await get_redis(settings.REDIS_URL)

    while True:
        messages = await queue.consume(settings.QUEUE_STREAM_EMBEDDINGS, settings.QUEUE_GROUP_EMBEDDINGS, consumer_name, count=10, block_ms=5000)
        if not messages:
            continue
        for msg_id, payload in messages:
            try:
                text = (payload or {}).get("text")
                if not text:
                    await queue.ack(settings.QUEUE_STREAM_EMBEDDINGS, settings.QUEUE_GROUP_EMBEDDINGS, msg_id)
                    continue
                resp = await client.embeddings.create(model=settings.EMBEDDING_MODEL, input=[text])
                vec = list(resp.data[0].embedding)
                # Optionally store somewhere or publish; here we just ack
                await queue.ack(settings.QUEUE_STREAM_EMBEDDINGS, settings.QUEUE_GROUP_EMBEDDINGS, msg_id)
            except Exception as e:
                logger.error(f"Embedding failed for message {msg_id}: {e}")


if __name__ == "__main__":
    asyncio.run(run_embedding_worker())


