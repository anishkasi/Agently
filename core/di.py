"""Dependency Injection container for MyAgent.

Provides centralized access to all services and dependencies.
"""

from typing import Any
from contextlib import asynccontextmanager

from core import settings
from core.logging import configure_json_logging

# DB session factory
from adapter.db.session import AsyncSessionLocal

# Cache
from adapter.cache.redis_cache import get_redis

# LLM
from adapter.llm.client import LLMClient

# Queue
from adapter.queue.redis_streams import RedisStreamsQueue


class Container:
    """Simple DI container exposing factories for core dependencies."""

    def __init__(self) -> None:
        configure_json_logging()
        self._services = {}

    @asynccontextmanager
    async def db(self):
        """Provide async DB session context manager."""
        async with AsyncSessionLocal() as session:
            yield session

    async def cache(self):
        """Get Redis client."""
        return await get_redis(settings.REDIS_URL)

    def llm(self) -> LLMClient:
        """Get LLM client instance."""
        return LLMClient(model=settings.OPENAI_MODEL)

    def queue(self) -> RedisStreamsQueue:
        """Get Redis Streams queue instance."""
        return RedisStreamsQueue()

    def get(self, name: str) -> Any:
        """
        Get a service by name (lazy initialization).
        
        Args:
            name: Service name (e.g., 'group_service', 'rag_service')
            
        Returns:
            Service instance
        """
        if name not in self._services:
            self._services[name] = self._create_service(name)
        return self._services[name]

    def _create_service(self, name: str) -> Any:
        """Factory method to create services on demand."""
        if name == "group_service":
            from service.group.group_service import GroupService
            return GroupService()
        elif name == "user_service":
            from service.group.user_service import UserService
            return UserService()
        elif name == "config_service":
            from service.group.config_service import ConfigService
            return ConfigService()
        elif name == "message_service":
            from service.message_service import MessageService
            return MessageService()
        elif name == "moderation_service":
            from service.moderation_service import ModerationService
            return ModerationService()
        elif name == "router_service":
            from service.router_service import RouterService
            return RouterService()
        elif name == "rag_service":
            from service.rag_service import RAGService
            return RAGService()
        else:
            raise ValueError(f"Unknown service: {name}")

    @asynccontextmanager
    async def get_async(self, name: str):
        """
        Get async resource (like db_session) as context manager.
        
        Args:
            name: Resource name (e.g., 'db_session')
            
        Yields:
            Resource instance
        """
        if name == "db_session":
            async with self.db() as session:
                yield session
        else:
            raise ValueError(f"Unknown async resource: {name}")


# Global container instance
container = Container()
