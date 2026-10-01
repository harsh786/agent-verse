"""Startup API-key sync: one cross-tenant query instead of one per tenant.

The lifespan's tenant sync issued three statements per tenant (set GUC, SELECT
keys, reset GUC) — ~1,200 round trips for ~400 tenants on every boot. With a
role allowed to bypass RLS (the system factory + ``system_session``) all active
keys load in one SELECT; under a NOBYPASSRLS role that SELECT raises, and the
sync falls back to the per-tenant RLS loop (never a silent empty key set).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.services.tenant_service import TenantService

N = 50


def _tenant(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"t{i}", name=f"T{i}", email=f"t{i}@x.io", plan_tier="free", created_at=None
    )


def _key(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"k{i}",
        tenant_id=f"t{i}",
        name="Default",
        scopes=[],
        roles=["admin"],
        expires_at=None,
        key_hash=f"h{i}",
        created_at=None,
    )


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, log: list[str], *, bypass_allowed: bool) -> None:
        self._log = log
        self._bypass_allowed = bypass_allowed
        self._tenant: str | None = None
        self._row_security_off = False

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, statement: object, params: dict[str, str] | None = None) -> _Result:
        sql = str(statement)
        if "row_security" in sql:
            self._row_security_off = True
            return _Result([])
        if "set_config('app.tenant_id'" in sql:
            self._tenant = (params or {}).get("tid") or None
            return _Result([])
        if "FROM tenants" in sql:
            return _Result([_tenant(i) for i in range(N)])
        if "FROM api_keys" in sql:
            self._log.append("keys")
            if self._row_security_off:
                if not self._bypass_allowed:
                    raise RuntimeError("query would be affected by row-level security policy")
                return _Result([_key(i) for i in range(N)])
            if self._tenant:
                return _Result([_key(int(self._tenant[1:]))])
            return _Result([])
        return _Result([])


async def test_keys_load_in_one_query_when_rls_bypass_is_allowed() -> None:
    log: list[str] = []
    svc = TenantService(
        db_session_factory=lambda: _Session(log, bypass_allowed=True),
        system_db_session_factory=lambda: _Session(log, bypass_allowed=True),
    )
    assert await svc.sync_from_db() == N
    assert log == ["keys"]
    assert len(svc._hash_to_key_id) == N
    assert svc._keys["k7"]["tenant_id"] == "t7"


async def test_falls_back_to_per_tenant_queries_under_nobypassrls() -> None:
    log: list[str] = []
    svc = TenantService(
        db_session_factory=lambda: _Session(log, bypass_allowed=False),
        system_db_session_factory=lambda: _Session(log, bypass_allowed=False),
    )
    assert await svc.sync_from_db() == N
    assert log.count("keys") == 1 + N  # the refused batch, then one per tenant
    assert len(svc._hash_to_key_id) == N
