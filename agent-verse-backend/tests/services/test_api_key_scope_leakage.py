"""Dedicated tenant/API-key scope-leakage tests.

Covers the specific security-boundary scenarios the in-memory-only tests in
test_tenant_service*.py don't exercise:

  * a correctly-formatted, *active* API key issued for tenant A cannot be used
    to reach tenant B's resources (via ``resolve_api_key`` returning the wrong
    tenant, or via revoking/administering another tenant's key);
  * revoked/deleted keys are rejected on next use through the DB-authoritative
    path (``_db_resolve_by_hash``), not just the in-memory fallback;
  * expired keys are rejected through the DB-authoritative path;
  * a deactivated tenant's keys stop resolving even if the key row itself is
    still marked active;
  * ``_db_revoke_api_key`` scopes both its SELECT and UPDATE to the caller's
    own tenant_id as defense-in-depth alongside RLS (regression test for the
    fix applied alongside this test file).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import NotFoundError
from app.services.tenant_service import TenantService

pytestmark = pytest.mark.asyncio


# ── in-memory: cross-tenant resource access via a correctly-formatted key ──────


async def test_tenant_a_key_does_not_resolve_to_tenant_b_context() -> None:
    """A correctly-formatted, active key for tenant A must always resolve to
    tenant A's own TenantContext, never leaking into tenant B's identity."""
    svc = TenantService()
    a = await svc.create_tenant(name="Tenant A", email="a@leak-test.com")
    b = await svc.create_tenant(name="Tenant B", email="b@leak-test.com")

    ctx_a = await svc.resolve_api_key(a["api_key"])
    ctx_b = await svc.resolve_api_key(b["api_key"])

    assert ctx_a is not None
    assert ctx_b is not None
    assert ctx_a.tenant_id == a["tenant_id"]
    assert ctx_b.tenant_id == b["tenant_id"]
    assert ctx_a.tenant_id != ctx_b.tenant_id


async def test_tenant_a_cannot_administer_tenant_b_key() -> None:
    """Tenant A cannot revoke, and does not see, a key that belongs to tenant B —
    even when A supplies a syntactically valid key_id belonging to B."""
    svc = TenantService()
    a = await svc.create_tenant(name="Tenant A", email="a2@leak-test.com")
    b = await svc.create_tenant(name="Tenant B", email="b2@leak-test.com")

    key_b = await svc.create_api_key(tenant_id=b["tenant_id"], name="B Key", scopes=["read"])

    with pytest.raises(NotFoundError):
        await svc.revoke_api_key(tenant_id=a["tenant_id"], key_id=key_b["key_id"])

    # B's key must still be fully active after A's failed revoke attempt.
    ctx = await svc.resolve_api_key(key_b["raw_key"])
    assert ctx is not None
    assert ctx.tenant_id == b["tenant_id"]

    keys_a = await svc.list_api_keys(tenant_id=a["tenant_id"])
    assert key_b["key_id"] not in {k["key_id"] for k in keys_a}


# ── revoked / expired keys rejected via the DB-authoritative path ──────────────


def _make_db_row_session(row: object | None) -> object:
    """Build a fake async session whose ``execute`` returns *row* for the
    ApiKey/Tenant join used by ``_db_resolve_by_hash``."""

    class _ScalarResult:
        def __init__(self, value: object | None) -> None:
            self._value = value

        def first(self) -> object | None:
            return self._value

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def begin(self) -> _Session:
            return self

        async def execute(self, *args: object, **kwargs: object) -> _ScalarResult:
            return _ScalarResult(row)

    return _Session


async def test_db_resolve_rejects_expired_key() -> None:
    """_db_resolve_by_hash (the multi-pod authoritative lookup) must reject an
    expired key even though the SQL filter only checks is_active."""
    key = SimpleNamespace(
        id="key-expired",
        expires_at=datetime.now(UTC) - timedelta(days=1),
        roles=["operator"],
    )
    tenant = SimpleNamespace(id="tenant-x", plan_tier="free", is_active=True)

    session_cls = _make_db_row_session((key, tenant))

    def fake_db_factory() -> object:
        return session_cls()

    svc = TenantService(db_session_factory=fake_db_factory)
    rec = await svc._db_resolve_by_hash("some-hash")
    assert rec is None


async def test_db_resolve_rejects_deactivated_tenant() -> None:
    """Even if a key row is active, a deactivated tenant must not resolve —
    otherwise a suspended tenant retains API access."""
    key = SimpleNamespace(id="key-1", expires_at=None, roles=["admin"])
    tenant = SimpleNamespace(id="tenant-deactivated", plan_tier="free", is_active=False)

    session_cls = _make_db_row_session((key, tenant))

    def fake_db_factory() -> object:
        return session_cls()

    svc = TenantService(db_session_factory=fake_db_factory)
    rec = await svc._db_resolve_by_hash("some-hash")
    assert rec is None


async def test_db_resolve_returns_none_for_unknown_hash() -> None:
    """A revoked/deleted key has no matching active row (the SQL query filters
    ApiKey.is_active == True), so the join simply returns no row."""
    session_cls = _make_db_row_session(None)

    def fake_db_factory() -> object:
        return session_cls()

    svc = TenantService(db_session_factory=fake_db_factory)
    rec = await svc._db_resolve_by_hash("revoked-or-missing-hash")
    assert rec is None


async def test_db_resolve_accepts_valid_active_key() -> None:
    """Sanity check: a valid, non-expired key on an active tenant does resolve,
    so the rejection tests above aren't vacuously true."""
    key = SimpleNamespace(id="key-ok", expires_at=None, roles=["operator"])
    tenant = SimpleNamespace(id="tenant-ok", plan_tier="starter", is_active=True)

    session_cls = _make_db_row_session((key, tenant))

    def fake_db_factory() -> object:
        return session_cls()

    svc = TenantService(db_session_factory=fake_db_factory)
    rec = await svc._db_resolve_by_hash("good-hash")
    assert rec is not None
    assert rec["tenant_id"] == "tenant-ok"
    assert rec["api_key_id"] == "key-ok"


# ── regression: _db_revoke_api_key must filter by tenant_id explicitly ─────────


async def test_db_revoke_api_key_scopes_queries_to_caller_tenant() -> None:
    """Regression test: _db_revoke_api_key must include an explicit tenant_id
    filter in both the SELECT and UPDATE it issues, as defense-in-depth
    alongside RLS. Without it, a revoke call scoped to tenant B's context but
    naming tenant A's key_id would rely solely on RLS to no-op — this test
    inspects the compiled statements to make sure the filter is present
    regardless of whether RLS is active in this (mocked) session."""

    captured_statements: list[str] = []

    class _Result:
        def scalar_one_or_none(self) -> None:
            return None

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def begin(self) -> _Session:
            return self

        async def execute(self, statement: object, *args: object, **kwargs: object) -> _Result:
            captured_statements.append(str(statement))
            return _Result()

    def fake_db_factory() -> _Session:
        return _Session()

    svc = TenantService(db_session_factory=fake_db_factory)
    result = await svc._db_revoke_api_key("some-key-id", "tenant-caller")

    assert result is None
    # Every statement issued against api_keys must reference tenant_id, not
    # just the primary key — this is the defense-in-depth filter.
    api_key_statements = [s for s in captured_statements if "api_keys" in s]
    assert api_key_statements, "expected at least one api_keys statement"
    for stmt in api_key_statements:
        assert "tenant_id" in stmt, f"missing tenant_id filter in: {stmt}"
