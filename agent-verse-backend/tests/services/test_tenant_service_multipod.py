"""TenantService must be DB-authoritative for tenant/key reads (multi-pod).

Regression: get_tenant / list_api_keys / create_api_key read only the pod's
in-memory copy, so a tenant created on pod A answered 404 on pod B for
GET /tenants/me and POST /tenants/me/keys (and listed no keys) until pod B
restarted and ran sync_from_db.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import NotFoundError
from app.services.tenant_service import TenantService


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeDB:
    """Minimal shared 'Postgres' visible to every pod's TenantService."""

    def __init__(self) -> None:
        self.tenants: dict[str, Any] = {}
        self.keys: list[Any] = []
        self.api_key_reads_guc: list[str] = []

    def __call__(self) -> _Session:
        return _Session(self)


class _Session:
    def __init__(self, db: _FakeDB) -> None:
        self._db = db
        self._tid = ""

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        if "set_config('app.tenant_id'" in sql:
            self._tid = (params or {}).get("tid", "")
            return _Result([])
        if "FROM api_keys" in sql:
            # RLS: only rows for the tenant GUC are visible.
            self._db.api_key_reads_guc.append(self._tid)
            rows = [k for k in self._db.keys if k.tenant_id == self._tid]
            return _Result(rows)
        if "FROM tenants" in sql:
            cmp = stmt.compile()
            wanted = next(
                (v for k, v in cmp.params.items() if k.startswith("id")), None
            )
            t = self._db.tenants.get(wanted)
            return _Result([t] if t is not None else [])
        return _Result([])

    def add(self, obj: Any) -> None:
        name = type(obj).__name__
        if name == "ApiKey":
            if obj.tenant_id != self._tid:
                raise RuntimeError("RLS violation: api_keys insert outside tenant GUC")
            obj.created_at = datetime.now(UTC)
            obj.is_active = True if obj.is_active is None else obj.is_active
            self._db.keys.append(obj)
        elif name == "Tenant":
            obj.created_at = datetime.now(UTC)
            obj.is_active = True
            self._db.tenants[obj.id] = obj


def _seed_tenant(db: _FakeDB, tid: str = "t-remote") -> None:
    db.tenants[tid] = SimpleNamespace(
        id=tid,
        name="Remote Co",
        email="remote@example.com",
        plan_tier="starter",
        is_active=True,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.keys.append(
        SimpleNamespace(
            id="k-remote",
            tenant_id=tid,
            name="Default",
            scopes=[],
            roles=["admin"],
            expires_at=None,
            key_hash="h" * 64,
            is_active=True,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )


@pytest.mark.asyncio
async def test_get_tenant_reads_db_when_not_in_pod_memory() -> None:
    db = _FakeDB()
    _seed_tenant(db)
    pod_b = TenantService(db_session_factory=db)
    tenant = await pod_b.get_tenant("t-remote")
    assert tenant["tenant_id"] == "t-remote"
    assert tenant["plan"] == "starter"
    assert tenant["email"] == "remote@example.com"
    with pytest.raises(NotFoundError):
        await pod_b.get_tenant("nope")


@pytest.mark.asyncio
async def test_list_api_keys_reads_db_under_tenant_rls() -> None:
    db = _FakeDB()
    _seed_tenant(db)
    pod_b = TenantService(db_session_factory=db)
    keys = await pod_b.list_api_keys("t-remote")
    assert [k["key_id"] for k in keys] == ["k-remote"]
    assert "key_hash" not in keys[0]
    assert db.api_key_reads_guc == ["t-remote"]


@pytest.mark.asyncio
async def test_create_api_key_on_other_pod_and_visible_everywhere() -> None:
    db = _FakeDB()
    pod_a = TenantService(db_session_factory=db)
    created = await pod_a.create_tenant(name="Acme", email="acme@example.com")
    tid = created["tenant_id"]

    pod_b = TenantService(db_session_factory=db)  # never saw the tenant in memory
    new_key = await pod_b.create_api_key(tenant_id=tid, name="CI", scopes=["goals:read"])
    assert new_key["raw_key"].startswith("av_free_")

    # Pod A (which did not create the key) lists it from the DB.
    listed = {k["key_id"] for k in await pod_a.list_api_keys(tid)}
    assert new_key["key_id"] in listed
    assert created["api_key_id"] in listed

    with pytest.raises(NotFoundError):
        await pod_b.create_api_key(tenant_id="ghost", name="x", scopes=[])


@pytest.mark.asyncio
async def test_no_db_keeps_in_memory_behaviour() -> None:
    svc = TenantService()
    created = await svc.create_tenant(name="Mem", email="mem@example.com")
    tid = created["tenant_id"]
    assert (await svc.get_tenant(tid))["name"] == "Mem"
    await svc.create_api_key(tenant_id=tid, name="k2", scopes=[])
    assert len(await svc.list_api_keys(tid)) == 2
