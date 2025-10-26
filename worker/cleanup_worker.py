import asyncio
import logging
from core import settings
from adapter.queue.redis_streams import RedisStreamsQueue


logger = logging.getLogger(__name__)


async def run_cleanup_worker():
    queue = RedisStreamsQueue(settings.REDIS_URL)
    await queue.create_group(settings.QUEUE_STREAM_CLEANUP, settings.QUEUE_GROUP_CLEANUP)
    consumer_name = "cleanup-1"

    while True:
        messages = await queue.consume(settings.QUEUE_STREAM_CLEANUP, settings.QUEUE_GROUP_CLEANUP, consumer_name, count=25, block_ms=5000)
        if not messages:
            continue
        for msg_id, payload in messages:
            try:
                # Placeholder: implement cleanup tasks (e.g., old cache keys, stale tasks)
                await queue.ack(settings.QUEUE_STREAM_CLEANUP, settings.QUEUE_GROUP_CLEANUP, msg_id)
            except Exception as e:
                logger.error(f"Cleanup failed for message {msg_id}: {e}")


if __name__ == "__main__":
    asyncio.run(run_cleanup_worker())


