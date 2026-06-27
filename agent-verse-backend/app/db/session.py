"""Async SQLAlchemy session factory and FastAPI dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def _make_engine(database_url: str | None = None) -> AsyncEngine:
    settings = get_settings()
    url = database_url or settings.database_url
    return create_async_engine(
        url,
        pool_pre_ping=getattr(settings, "db_pool_pre_ping", True),
        pool_size=getattr(settings, "db_pool_size", 10),
        max_overflow=getattr(settings, "db_max_overflow", 20),
        pool_timeout=getattr(settings, "db_pool_timeout", 30),
        pool_recycle=getattr(settings, "db_pool_recycle", 1800),
        # Disable prepared statement caching for PgBouncer transaction mode
        connect_args={"statement_cache_size": 0},
    )


def _make_session_factory(database_url: str | None = None) -> async_sessionmaker[AsyncSession]:
    engine = _make_engine(database_url)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# Module-level lazy singleton — replaced in tests via dependency override
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = _make_session_factory()
    return _session_factory


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Context-manager that yields an AsyncSession and commits/rolls back."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields one session per request."""
    async with get_db_session() as session:
        yield session
