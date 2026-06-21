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


@asynccontextmanager
async def rls_context(conn: asyncpg.Connection, tenant_id: str) -> AsyncIterator[None]:
    """Set ``app.tenant_id`` GUC for the duration of the calling transaction.

    Must be used inside an open transaction (asyncpg ``async with conn.transaction()``).
    The SET LOCAL is transaction-scoped — rolls back automatically.
    """
    await conn.execute("SET LOCAL app.tenant_id = $1", tenant_id)
    try:
        yield
    finally:
        # ``SET LOCAL`` resets automatically when the transaction ends, but an
        # explicit reset guards against callers that use savepoints.
        await conn.execute("SET LOCAL app.tenant_id = ''")
