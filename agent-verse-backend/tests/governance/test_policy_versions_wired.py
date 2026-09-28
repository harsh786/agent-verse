"""Policy versions are written on create/delete; rollback actually restores.

policy_versions was never written by app code; rollback appended a version row
but never touched governance_policies or the engine; queries filtered by
policy_id only (no tenant).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from app.api.governance import router
from app.governance.policies import PolicyEngine
from tests.governance._router_app import make_app


class _Res:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _DB:
    """Records every statement; answers the few SELECTs the code under test runs."""

    def __init__(self, target: tuple[Any, ...] | None = None, fail_on: str = "") -> None:
        self.stmts: list[tuple[str, dict[str, Any]]] = []
        self.target = target
        self.fail_on = fail_on
        self.policies_rows: list[tuple[Any, ...]] = []

    def __call__(self) -> Any:
        s = MagicMock()
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=False)
        b = MagicMock()
        b.__aenter__ = AsyncMock(return_value=s)
        b.__aexit__ = AsyncMock(return_value=False)
        s.begin = MagicMock(return_value=b)
        s.execute = AsyncMock(side_effect=self._execute)
        return s

    async def _execute(self, q: Any, params: Any = None) -> _Res:
        sql = " ".join(str(q).split())
        self.stmts.append((sql, dict(params or {})))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("db down")
        if "COALESCE(MAX(version_number)" in sql:
            return _Res(scalar=3)
        if "FROM policy_versions" in sql and "version_number = :ver" in sql:
            return _Res([self.target] if self.target else [])
        if "SELECT name, action, tools_pattern, tenant_id FROM governance_policies" in sql:
            return _Res(self.policies_rows)
        return _Res()

    def sql(self, needle: str) -> list[tuple[str, dict[str, Any]]]:
        return [(q, p) for q, p in self.stmts if needle in q]


def _app(db: _DB) -> Any:
    app = make_app(router)
    app.state.db_session_factory = db
    app.state.policy_engine = PolicyEngine()
    return app


async def test_create_policy_writes_v1_snapshot_in_same_tx() -> None:
    db = _DB()
    app = _app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/governance/policies",
            json={"name": "no-deploy", "tools_pattern": "deploy_*", "action": "deny"},
        )
    assert r.status_code == 201, r.text
    ins = db.sql("INSERT INTO policy_versions")
    assert len(ins) == 1
    params = ins[0][1]
    assert params["tid"] == "t-gov" and params["pid"] == r.json()["policy_id"]
    assert json.loads(params["rules"])[0]["tools_pattern"] == "deploy_*"
    assert params["active"] is True
    # Tenant-scoped deactivate/max queries.
    assert all(
        "tenant_id = :tid" in q for q, _ in db.sql("policy_versions") if "INSERT" not in q
    )


async def test_create_policy_db_failure_is_503_and_not_enforced() -> None:
    db = _DB(fail_on="INSERT INTO governance_policies")
    app = _app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/governance/policies",
            json={"name": "p", "tools_pattern": "x", "action": "deny"},
        )
    assert r.status_code == 503
    assert app.state.policy_engine._policies == []


async def test_versions_listing_is_tenant_scoped_under_rls() -> None:
    db = _DB()
    app = _app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/governance/policies/p1/versions")
    assert r.status_code == 200
    q, params = db.sql("FROM policy_versions")[0]
    assert "tenant_id = :tid" in q and params == {"tid": "t-gov", "pid": "p1"}
    assert "set_config('app.tenant_id'" in db.stmts[0][0]


async def test_versions_listing_db_error_is_503() -> None:
    db = _DB(fail_on="FROM policy_versions")
    app = _app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/governance/policies/p1/versions")
    assert r.status_code == 503


async def test_rollback_restores_governance_policies_and_engine_and_publishes() -> None:
    rules = [{"tools_pattern": "delete_*", "action": "deny", "priority": 5}]
    db = _DB(target=("v1", "no-delete", "old", rules, 1, None))
    db.policies_rows = [("no-delete", "deny", "delete_*", "t-gov")]
    app = _app(db)
    redis = MagicMock()
    redis.publish = AsyncMock()
    app.state._policy_pubsub_redis = redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/governance/policies/p1/rollback", json={"target_version": 1, "reason": "oops"}
        )
    assert r.status_code == 200, r.text
    assert r.json()["new_version"] == 4
    upsert = db.sql("INSERT INTO governance_policies")
    assert upsert and upsert[0][1]["pattern"] == "delete_*" and upsert[0][1]["tid"] == "t-gov"
    # Target lookup is tenant-scoped.
    q, params = db.sql("version_number = :ver")[0]
    assert "tenant_id = :tid" in q and params["tid"] == "t-gov"
    # Engine now enforces the restored policy.
    from app.governance.policies import PolicyResult
    from tests.governance._router_app import tenant

    assert app.state.policy_engine.evaluate("delete_repo", tenant_ctx=tenant()) == (
        PolicyResult.DENY
    )
    redis.publish.assert_awaited()
    assert json.loads(redis.publish.call_args.args[1])["action"] == "rolled_back"


async def test_rollback_to_deletion_snapshot_removes_policy() -> None:
    from datetime import UTC, datetime

    db = _DB(target=("v2", "p", "", [], 2, datetime.now(UTC)))
    app = _app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/governance/policies/p1/rollback", json={"target_version": 2, "reason": "x"}
        )
    assert r.status_code == 200, r.text
    assert r.json()["policy_deleted"] is True
    assert db.sql("DELETE FROM governance_policies")
