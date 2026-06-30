"""Unit tests for TenantService."""

from __future__ import annotations

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.services.tenant_service import TenantService


@pytest.fixture
def svc() -> TenantService:
    return TenantService()


# ── create_tenant ─────────────────────────────────────────────────────────────


async def test_create_tenant_returns_raw_key(svc: TenantService) -> None:
    result = await svc.create_tenant(name="Acme Corp", email="admin@acme.com")
    assert "api_key" in result
    raw_key: str = result["api_key"]
    assert raw_key.startswith("av_free_"), f"unexpected prefix: {raw_key!r}"
    assert len(raw_key) > 10
    assert "tenant_id" in result
    assert result["name"] == "Acme Corp"
    assert result["email"] == "admin@acme.com"
    assert result["plan"] == "free"


async def test_create_tenant_duplicate_email_raises_conflict(svc: TenantService) -> None:
    await svc.create_tenant(name="First", email="dupe@example.com")
    with pytest.raises(ConflictError, match="already registered"):
        await svc.create_tenant(name="Second", email="dupe@example.com")


async def test_create_tenant_email_comparison_is_case_insensitive(svc: TenantService) -> None:
    await svc.create_tenant(name="First", email="User@Example.COM")
    with pytest.raises(ConflictError):
        await svc.create_tenant(name="Second", email="user@example.com")


# ── get_tenant ────────────────────────────────────────────────────────────────


async def test_get_tenant_not_found(svc: TenantService) -> None:
    with pytest.raises(NotFoundError):
        await svc.get_tenant(tenant_id="nonexistent-id")


async def test_get_tenant_returns_profile(svc: TenantService) -> None:
    created = await svc.create_tenant(name="My Corp", email="me@corp.com")
    tenant = await svc.get_tenant(tenant_id=created["tenant_id"])
    assert tenant["name"] == "My Corp"
    assert tenant["email"] == "me@corp.com"
    assert tenant["plan"] == "free"
    assert "tenant_id" in tenant


# ── list_api_keys ─────────────────────────────────────────────────────────────


async def test_list_api_keys_excludes_raw(svc: TenantService) -> None:
    created = await svc.create_tenant(name="Corp", email="listkeys@example.com")
    tid = created["tenant_id"]
    keys = await svc.list_api_keys(tenant_id=tid)
    assert len(keys) >= 1
    for k in keys:
        assert "raw_key" not in k, "raw_key must never appear in list response"
        assert "key_hash" not in k, "key_hash must never appear in list response"
        assert "api_key" not in k, "api_key must never appear in list response"
        assert "key_id" in k
        assert "is_active" in k


async def test_list_api_keys_empty_for_unknown_tenant(svc: TenantService) -> None:
    keys = await svc.list_api_keys(tenant_id="no-such-tenant")
    assert keys == []


# ── create_api_key / revoke ───────────────────────────────────────────────────


async def test_create_and_revoke_key(svc: TenantService) -> None:
    created = await svc.create_tenant(name="Corp", email="revoke@example.com")
    tid = created["tenant_id"]

    new_key = await svc.create_api_key(tenant_id=tid, name="CI Key", scopes=["read"])
    assert "raw_key" in new_key
    assert new_key["is_active"] is True
    kid = new_key["key_id"]
    raw = new_key["raw_key"]

    # Before revocation the key should resolve to a TenantContext
    ctx = await svc.resolve_api_key(raw)
    assert ctx is not None
    assert ctx.tenant_id == tid
    assert ctx.api_key_id == kid

    # After revocation it should no longer resolve
    await svc.revoke_api_key(tenant_id=tid, key_id=kid)
    assert await svc.resolve_api_key(raw) is None


async def test_revoke_nonexistent_key_raises(svc: TenantService) -> None:
    created = await svc.create_tenant(name="Corp", email="revokenf@example.com")
    with pytest.raises(NotFoundError):
        await svc.revoke_api_key(tenant_id=created["tenant_id"], key_id="ghost-id")


# ── resolve_api_key ───────────────────────────────────────────────────────────


async def test_resolve_api_key_valid(svc: TenantService) -> None:
    result = await svc.create_tenant(name="Corp", email="resolve@example.com")
    ctx = await svc.resolve_api_key(result["api_key"])
    assert ctx is not None
    assert ctx.tenant_id == result["tenant_id"]
    assert ctx.api_key_id == result["api_key_id"]


async def test_resolve_api_key_invalid_returns_none(svc: TenantService) -> None:
    ctx = await svc.resolve_api_key("av_free_completelyfakekey12345nope")
    assert ctx is None


async def test_resolve_api_key_wrong_format_returns_none(svc: TenantService) -> None:
    assert await svc.resolve_api_key("") is None
    assert await svc.resolve_api_key("not-a-key") is None


# ── tenant isolation ──────────────────────────────────────────────────────────


async def test_tenant_isolation(svc: TenantService) -> None:
    """API keys from tenant A cannot be revoked or listed by tenant B."""
    a = await svc.create_tenant(name="Corp A", email="a@isolation.com")
    b = await svc.create_tenant(name="Corp B", email="b@isolation.com")

    key_a = await svc.create_api_key(tenant_id=a["tenant_id"], name="A Key", scopes=[])
    kid_a = key_a["key_id"]

    # Tenant B cannot revoke tenant A's key
    with pytest.raises(NotFoundError):
        await svc.revoke_api_key(tenant_id=b["tenant_id"], key_id=kid_a)

    # Tenant B's key list doesn't include tenant A's key
    keys_b = await svc.list_api_keys(tenant_id=b["tenant_id"])
    b_key_ids = {k["key_id"] for k in keys_b}
    assert kid_a not in b_key_ids


# ── roles regression ──────────────────────────────────────────────────────────


async def test_create_tenant_initial_key_has_admin_role(svc: TenantService) -> None:
    """Initial API key must resolve with roles=('admin',) not the operator default.

    Regression: create_tenant() stored roles=['admin'] in memory but
    sync_from_db() never loaded them, so after the lifespan upgrade to the
    DB-backed service all keys defaulted to ('operator',) — losing
    governance:read, costs:read, etc.
    """
    result = await svc.create_tenant(name="AdminCorp", email="admin@admincorp.com")
    ctx = await svc.resolve_api_key(result["api_key"])

    assert ctx is not None
    assert "admin" in ctx.roles, (
        f"Initial owner key must have 'admin' role; got roles={ctx.roles!r}"
    )
