"""PostgreSQL Row-Level Security context manager.

Sets the ``app.tenant_id`` GUC (per-connection) so RLS policies can filter
rows to the current tenant. Uses ``SET LOCAL`` so the setting is automatically
reverted when the transaction ends — no explicit cleanup needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg
    from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def rls_context(conn: asyncpg.Connection, tenant_id: str) -> AsyncIterator[None]:
    """Set ``app.tenant_id`` GUC for the duration of the calling transaction.

    Must be used inside an open transaction (asyncpg ``async with conn.transaction()``).
    The SET LOCAL is transaction-scoped — rolls back automatically.
    """
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
    try:
        yield
    finally:
        # ``SET LOCAL`` resets automatically when the transaction ends, but an
        # explicit reset guards against callers that use savepoints.
        await conn.execute("SELECT set_config('app.tenant_id', '', true)")


@asynccontextmanager
async def sqlalchemy_rls_context(
    session: AsyncSession, tenant_id: str
) -> AsyncIterator[AsyncSession]:
    """Set app.tenant_id RLS variable for a SQLAlchemy AsyncSession.

    Must be called AFTER session.begin() or inside an existing transaction.
    Uses SET LOCAL so it's transaction-scoped.
    """
    from sqlalchemy import text

    await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
    try:
        yield session
    finally:
        # SET LOCAL auto-resets when transaction ends, but reset explicitly for safety
        try:
            await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        except Exception:
            pass


@asynccontextmanager
async def system_session(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Set session for system-level maintenance, bypassing tenant RLS.

    Issues ``SET LOCAL row_security = off`` so the calling transaction can
    read/write rows across all tenants without RLS filtering.  Falls back to
    setting a recognisable ``__system__`` marker if the DB role lacks the
    ``BYPASSRLS`` privilege (the system marker can be matched by a permissive
    superuser-equivalent RLS policy when BYPASSRLS is unavailable).

    Must be used **inside** an open transaction (i.e., after ``session.begin()``
    or inside an ``async with session.begin()`` block) so that ``SET LOCAL``
    is transaction-scoped and automatically reverts on commit/rollback.

    Usage::

        async with db() as session, session.begin():
            async with system_session(session):
                await session.execute(text("UPDATE goals SET ..."))
    """
    from sqlalchemy import text

    try:
        await session.execute(text("SET LOCAL row_security = off"))
    except Exception:
        # Fallback for roles without BYPASSRLS: set a recognisable system GUC.
        # A corresponding permissive RLS policy on each table can allow this value.
        with suppress(Exception):
            await session.execute(
                text("SELECT set_config('app.tenant_id', '__system__', true)")
            )
    yield session
