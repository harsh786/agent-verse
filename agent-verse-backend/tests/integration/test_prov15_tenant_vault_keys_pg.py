"""PROV-15: the tenant_vault_keys migration applies and the table is tenant-isolated."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_tenant_vault_keys_table_has_forced_rls(pg_url: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(pg_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = 'tenant_vault_keys'"
                    )
                )
            ).fetchone()
            policies = (
                await conn.execute(
                    text("SELECT policyname FROM pg_policies WHERE tablename = 'tenant_vault_keys'")
                )
            ).fetchall()
    finally:
        await engine.dispose()
    assert row is not None and row[0] is True and row[1] is True
    assert [p[0] for p in policies] == ["tenant_vault_keys_tenant_isolation"]
