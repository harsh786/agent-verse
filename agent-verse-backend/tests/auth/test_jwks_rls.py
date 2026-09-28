"""JWKS must see the signing keys under the least-privilege (NOBYPASSRLS) DB role.

Regression: ``_build_jwks`` ran ``SELECT ... FROM agent_credentials`` on the
request session factory with no ``app.tenant_id`` GUC. ``agent_credentials`` has
FORCE ROW LEVEL SECURITY, so under the production NOBYPASSRLS role the query saw
zero rows and ``/.well-known/jwks.json`` published an empty key set — every
verifier then rejected every agent JWT. The warm-up beat task additionally cached
that empty set for 10 minutes.

The platform-wide key set is now read through the maintenance/system session
(``SET LOCAL row_security = off`` on the BYPASSRLS factory), and a per-tenant key
set (``?tenant_id=``) is read under that tenant's RLS context.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.system import router as system_router
from app.auth.agent_identity import _build_jwks, generate_agent_keypair

_, PUBLIC_PEM = generate_agent_keypair()


def _rls_db(rows_by_tenant: dict[str, list[tuple[str, str]]]) -> tuple[Any, list[str]]:
    """Fake session factory that behaves like FORCE RLS: rows are visible only
    after ``row_security = off`` (all) or ``set_config('app.tenant_id', t)`` (t's)."""
    executed: list[str] = []
    state: dict[str, Any] = {"bypass": False, "tenant": ""}

    def factory() -> Any:
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        state.update(bypass=False, tenant="")

        async def _execute(stmt: Any, params: Any = None) -> Any:
            sql = str(stmt)
            executed.append(sql)
            if "row_security = off" in sql:
                state["bypass"] = True
            if "set_config('app.tenant_id'" in sql:
                state["tenant"] = (params or {}).get("tid", "")
            result = MagicMock()
            if "agent_credentials" in sql:
                if state["bypass"]:
                    rows = [r for rs in rows_by_tenant.values() for r in rs]
                else:
                    rows = rows_by_tenant.get(state["tenant"], [])
                result.fetchall = MagicMock(return_value=rows)
            return result

        session.execute = AsyncMock(side_effect=_execute)
        return session

    return factory, executed


@pytest.mark.asyncio
async def test_platform_jwks_reads_through_system_session() -> None:
    db, executed = _rls_db({"t1": [("kid-1", PUBLIC_PEM)], "t2": [("kid-2", PUBLIC_PEM)]})
    keys = await _build_jwks(db)
    assert sorted(k["kid"] for k in keys) == ["kid-1", "kid-2"]
    assert any("row_security = off" in s for s in executed)


@pytest.mark.asyncio
async def test_tenant_jwks_reads_under_tenant_rls() -> None:
    db, executed = _rls_db({"t1": [("kid-1", PUBLIC_PEM)], "t2": [("kid-2", PUBLIC_PEM)]})
    keys = await _build_jwks(db, tenant_id="t2")
    assert [k["kid"] for k in keys] == ["kid-2"]
    assert not any("row_security = off" in s for s in executed)


def _app(**state: Any) -> TestClient:
    app = FastAPI()
    app.include_router(system_router)
    for key, value in state.items():
        setattr(app.state, key, value)
    return TestClient(app)


def test_jwks_endpoint_uses_system_factory_not_request_factory() -> None:
    system_db, _ = _rls_db({"t1": [("kid-1", PUBLIC_PEM)]})

    def request_db() -> Any:  # the request-path factory must not be used here
        raise AssertionError("JWKS used the request (tenant-RLS) session factory")

    svc = MagicMock()
    svc._db = request_db
    client = _app(agent_identity_service=svc, system_db_session_factory=system_db)
    r = client.get("/.well-known/jwks.json")
    assert r.status_code == 200
    assert [k["kid"] for k in r.json()["keys"]] == ["kid-1"]


def test_jwks_endpoint_per_tenant_param() -> None:
    system_db, _ = _rls_db({"t1": [("kid-1", PUBLIC_PEM)], "t2": [("kid-2", PUBLIC_PEM)]})
    client = _app(system_db_session_factory=system_db)
    r = client.get("/.well-known/jwks.json", params={"tenant_id": "t1"})
    assert [k["kid"] for k in r.json()["keys"]] == ["kid-1"]
