"""OAUTH-DECRYPT-FAILOPEN on real Postgres: an undecryptable stored token marks the
connection (``oauth_tokens.needs_reauth``), is refused everywhere until a new token
is stored, and is never returned as the token."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.integration.test_vault_rotate_all_stores_pg import _insert

pytestmark = pytest.mark.integration


@pytest.fixture
async def db(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def _no_key(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("app.providers.tenant_vault.ensure_tenant_vault", _no_key)
    engine = create_async_engine(pg_url)
    tenant = "reauth-" + uuid.uuid4().hex[:8]
    async with engine.begin() as conn:
        await _insert(conn, "tenants", {"id": tenant, "name": tenant, "email": f"{tenant}@x.io"})
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), tenant
    finally:
        await engine.dispose()


async def test_undecryptable_token_marks_the_connection_until_reauthorized(db: Any) -> None:
    from sqlalchemy import text

    from app.mcp.oauth import OAuthFlowManager, OAuthReauthorizationRequiredError, OAuthToken
    from app.providers.vault import CredentialVault

    factory, tenant = db
    writer = OAuthFlowManager(vault=CredentialVault(master_key="key-that-was-lost"))
    writer._db_session_factory = factory
    await writer._persist_token_to_db(tenant, "srv", OAuthToken(access_token="ya29.secret"))

    reader = OAuthFlowManager(vault=CredentialVault(master_key="current-key"))
    reader._db_session_factory = factory
    with pytest.raises(OAuthReauthorizationRequiredError):
        await reader.aget_token(tenant, "srv")

    async def _flag() -> bool:
        async with factory() as s, s.begin():
            return bool(
                (
                    await s.execute(
                        text("SELECT needs_reauth FROM oauth_tokens WHERE tenant_id = :t"),
                        {"t": tenant},
                    )
                ).scalar_one()
            )

    assert await _flag() is True
    # Another replica / the worker refuses it too (no decrypt retried).
    other = OAuthFlowManager(vault=CredentialVault(master_key="current-key"))
    other._db_session_factory = factory
    with pytest.raises(OAuthReauthorizationRequiredError):
        await other.aget_token(tenant, "srv")

    # Re-authorizing stores a new token and clears the mark.
    await reader._persist_token_to_db(tenant, "srv", OAuthToken(access_token="ya29.new"))
    assert await _flag() is False
    reader._drop_cached((tenant, "srv"))
    token = await reader.aget_token(tenant, "srv")
    assert token is not None and token.access_token == "ya29.new"
