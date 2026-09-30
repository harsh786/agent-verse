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


def _server_settings(settings: object) -> dict[str, str]:
    """Server-side safety timeouts, sent as asyncpg startup parameters (per-connection GUCs).

    idle_in_transaction_session_timeout reclaims connections a cancelled request
    left mid-transaction (custom BaseHTTPMiddleware cancels the task on client
    disconnect without rolling back) — otherwise they leak and eventually
    exhaust the pool, causing the intermittent request hangs / "blips".
    A PgBouncer in front must list every key here in ignore_startup_parameters,
    or it refuses the connection (see infra/docker-compose.yml).
    """
    server_settings: dict[str, str] = {}
    idle_ms = int(getattr(settings, "db_idle_in_transaction_timeout_ms", 30_000) or 0)
    if idle_ms > 0:
        server_settings["idle_in_transaction_session_timeout"] = str(idle_ms)
    stmt_ms = int(getattr(settings, "db_statement_timeout_ms", 60_000) or 0)
    if stmt_ms > 0:
        server_settings["statement_timeout"] = str(stmt_ms)
    return server_settings


def _make_engine(database_url: str | None = None) -> AsyncEngine:
    settings = get_settings()
    url = database_url or settings.database_url
    server_settings = _server_settings(settings)

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


_system_engine: AsyncEngine | None = None
_system_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_system_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory for cross-tenant SYSTEM work only (never request paths).

    Bound to ``MAINTENANCE_DATABASE_URL`` — a role with BYPASSRLS — when set,
    otherwise the regular factory. ``app.db.rls.system_session`` must be used on
    sessions from this factory: under the NOBYPASSRLS application role it fails
    loudly ("query would be affected by row-level security"), which is exactly
    what previously broke every beat scan and startup warm-up once the API ran
    least-privilege.
    """
    global _system_engine, _system_session_factory
    url = (get_settings().maintenance_database_url or "").strip()
    if not url:
        return get_session_factory()
    if _system_session_factory is None:
        _system_engine = _make_engine(url)
        _system_session_factory = async_sessionmaker(
            _system_engine, expire_on_commit=False, class_=AsyncSession
        )
    return _system_session_factory


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
    global _engine, _session_factory, _system_engine, _system_session_factory
    if _engine is not None:
        await _engine.dispose()
    if _system_engine is not None:
        await _system_engine.dispose()
    _system_engine = None
    _system_session_factory = None
    # Drop the references so the NEXT event loop builds a fresh engine instead of
    # reusing this one. A Celery worker runs each task on its own loop (see
    # _run_async), and asyncpg connections are loop-bound: reusing a disposed
    # engine across loops raises "got Future attached to a different loop". After
    # disposing, resetting to None makes get_session_factory() rebuild per loop.
    _engine = None
    _session_factory = None


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Context-manager that yields an AsyncSession and commits/rolls back.

    Cleanup catches ``BaseException`` — not just ``Exception`` — because a client
    disconnect propagates ``asyncio.CancelledError`` (a ``BaseException``), and a
    bare ``except Exception`` would skip the rollback and leave the pooled
    connection stuck ``idle in transaction`` (the DB connection leak behind the
    request-hang "blips"). The rollback is shielded so the cancellation that is
    tearing the task down cannot also abort the rollback mid-await; the original
    exception is always re-raised.
    """
    import asyncio
    import contextlib

    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            with contextlib.suppress(Exception):
                await asyncio.shield(session.rollback())
            raise


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields one session per request."""
    async with get_db_session() as session:
        yield session
