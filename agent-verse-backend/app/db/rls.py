"""PostgreSQL Row-Level Security context manager.

Sets the ``app.tenant_id`` GUC (per-connection) so RLS policies can filter
rows to the current tenant. Uses ``SET LOCAL`` so the setting is automatically
reverted when the transaction ends — no explicit cleanup needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
