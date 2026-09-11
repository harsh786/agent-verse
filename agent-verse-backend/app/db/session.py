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

    # Server-side safety timeouts (asyncpg applies these as per-connection GUCs).
    # idle_in_transaction_session_timeout reclaims connections a cancelled request
    # left mid-transaction (custom BaseHTTPMiddleware cancels the task on client
    # disconnect without rolling back) — otherwise they leak and eventually
    # exhaust the pool, causing the intermittent request hangs / "blips".
    server_settings: dict[str, str] = {}
    idle_ms = int(getattr(settings, "db_idle_in_transaction_timeout_ms", 30_000) or 0)
    if idle_ms > 0:
        server_settings["idle_in_transaction_session_timeout"] = str(idle_ms)
    stmt_ms = int(getattr(settings, "db_statement_timeout_ms", 60_000) or 0)
    if stmt_ms > 0:
        server_settings["statement_timeout"] = str(stmt_ms)

    connect_args: dict[str, object] = {"statement_cache_size": 0}
    if server_settings:
        connect_args["server_settings"] = server_settings

    return create_async_engine(
        url,
        pool_pre_ping=getattr(settings, "db_pool_pre_ping", True),
        pool_size=getattr(settings, "db_pool_size", 10),
        max_overflow=getattr(settings, "db_max_overflow", 20),
        pool_timeout=getattr(settings, "db_pool_timeout", 30),
        pool_recycle=getattr(settings, "db_pool_recycle", 1800),
        # Disable prepared statement caching for PgBouncer transaction mode
        connect_args=connect_args,
    )


def _make_session_factory(database_url: str | None = None) -> async_sessionmaker[AsyncSession]:
    engine = _make_engine(database_url)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# Module-level lazy singletons — replaced in tests via dependency override.
# _engine is tracked separately so Celery tasks can call dispose_task_engine()
# before closing the event loop, preventing "Event loop is closed" errors
# from asyncpg connection pool teardown.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory, _engine
    if _session_factory is None:
        _engine = _make_engine()
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _session_factory


async def dispose_task_engine() -> None:
    """Dispose all pooled asyncpg connections owned by the module-level engine.

    Celery tasks MUST call this before their event loop is closed.  asyncpg
    connection cleanup requires an active event loop; if the loop is closed
    first SQLAlchemy raises ``RuntimeError: Event loop is closed`` for every
    pooled connection.

    Usage (inside _run_async / asyncio.run wrappers)::

        loop.run_until_complete(dispose_task_engine())
        loop.close()
    """
    if _engine is not None:
        await _engine.dispose()


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
