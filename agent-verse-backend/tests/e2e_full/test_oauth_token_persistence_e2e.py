"""e2e_full: OAuth tokens survive a restart (real schema, least-privilege roles).

Persistence never worked: the upsert's ON CONFLICT (tenant_id, server_id) had no
matching unique constraint, server_id had an FK to mcp_servers (which the Redis
connector registry never writes), the write set no tenant RLS context, and the
restore ran cross-tenant on the app role with obtained_at=0 (always expired).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def test_oauth_token_round_trips_through_postgres(
    _backends: tuple[str, str], _migrated_backends: tuple[str, str]
) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.mcp.oauth import OAuthFlowManager, OAuthToken

    owner_url, _ = _backends
    app_url, _ = _migrated_backends
    maint_url = os.environ.get("MAINTENANCE_DATABASE_URL") or app_url
    engines = [create_async_engine(u) for u in (owner_url, app_url, maint_url)]
    owner, app_f, maint_f = (async_sessionmaker(e, expire_on_commit=False) for e in engines)
    try:
        tenant = uuid.uuid4().hex
        async with owner() as s, s.begin():
            await s.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:t, 'oauth', :e)"),
                {"t": tenant, "e": f"{tenant}@oauth.test"},
            )
        writer = OAuthFlowManager()
        writer._db_session_factory = app_f
        await writer._persist_token_to_db(
            tenant, "srv-1", OAuthToken(access_token="at-1", refresh_token="rt-1", scope="read")
        )
        # Upsert: a refreshed token replaces the row.
        await writer._persist_token_to_db(tenant, "srv-1", OAuthToken(access_token="at-2"))
        async with owner() as s:
            n = (
                await s.execute(
                    text("SELECT COUNT(*) FROM oauth_tokens WHERE tenant_id = :t"), {"t": tenant}
                )
            ).scalar()
        assert n == 1

        restarted = OAuthFlowManager()
        restarted._db_session_factory = app_f
        restarted._system_session_factory = maint_f
        assert await restarted.load_tokens_from_db() >= 1
        tok = restarted._tokens.get((tenant, "srv-1"))
        assert tok is not None and tok.access_token == "at-2"
        assert not tok.is_expired()
    finally:
        for e in engines:
            await e.dispose()
