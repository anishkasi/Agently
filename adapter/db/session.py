from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from core import settings


engine = create_async_engine(
    settings.DATABASE_URL.replace("+psycopg2", "+asyncpg"),
    echo=settings.SQLALCHEMY_ECHO,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={"server_settings": {"timezone": "UTC"}},
    future=True,
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

Base = declarative_base()


