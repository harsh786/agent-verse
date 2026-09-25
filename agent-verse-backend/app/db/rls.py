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

    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
    )
    try:
        yield session
    finally:
        # SET LOCAL auto-resets when transaction ends, but reset explicitly for safety
        with suppress(Exception):
            await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))


@asynccontextmanager
async def system_session(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Set session for system-level maintenance, bypassing tenant RLS.

    Issues ``SET LOCAL row_security = off`` so the calling transaction can
    read/write rows across all tenants without RLS filtering.

    **This requires a role with BYPASSRLS (or superuser).** Provision the
    maintenance/Celery worker accordingly. Under a role without it, ``SET LOCAL
    row_security = off`` still *succeeds* and every subsequent statement then
    fails with ``InsufficientPrivilegeError: query would be affected by
    row-level security``. That is the intended outcome: a maintenance job that
    cannot see the rows must fail loudly rather than report success.

    A previous version claimed to "fall back to a recognisable ``__system__``
    marker if the DB role lacks BYPASSRLS". That fallback was unreachable — it
    was keyed on the ``SET`` itself raising, which it never does (verified: the
    SET succeeds and the *query* raises). It was also worse than failing: no
    table's policy matches ``__system__``, so it would have turned a loud
    permission error into every maintenance DELETE silently matching zero rows
    and reporting ``{"status": "ok", "deleted": 0}``. It is removed rather than
    repaired.

    Must be used **inside** an open transaction (i.e., after ``session.begin()``
    or inside an ``async with session.begin()`` block) so that ``SET LOCAL``
    is transaction-scoped and automatically reverts on commit/rollback.

    Usage::

        async with db() as session, session.begin():
            async with system_session(session):
                await session.execute(text("UPDATE goals SET ..."))
    """
    from sqlalchemy import text

    await session.execute(text("SET LOCAL row_security = off"))
    yield session
