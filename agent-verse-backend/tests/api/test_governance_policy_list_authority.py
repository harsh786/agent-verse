"""QA-13: with a DB configured, the DB's policy list is authoritative.

``list_policies`` fell back to this replica's in-memory registry whenever the DB
query returned ``[]`` — both on a DB error (swallowed into ``[]``) and when the
tenant legitimately had no policies — so a replica served policies another
replica had deleted. Now: DB configured → its result is returned as-is (an empty
list is a valid answer), a DB error is a 503; only a build with no DB at all
uses the in-memory registry.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.governance import router
from app.governance.policies import PolicyEngine
from tests.governance._router_app import make_app
from tests.governance.test_policy_versions_wired import _DB, _app

pytestmark = pytest.mark.asyncio

_STALE = {
    "policy_id": "stale-1",
    "name": "deleted-on-another-replica",
    "tools_pattern": "deploy*",
    "action": "deny",
    "priority": 0,
    "description": "",
}


def _with_stale_registry(app: Any) -> Any:
    app.state._policy_registry = {"t-gov": {"stale-1": dict(_STALE)}}
    return app


async def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_db_with_no_policies_is_an_empty_list_not_the_stale_registry() -> None:
    app = _with_stale_registry(_app(_DB()))
    async with await _client(app) as c:
        r = await c.get("/governance/policies")
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_db_error_is_503_not_the_stale_registry() -> None:
    app = _with_stale_registry(_app(_DB(fail_on="FROM governance_policies")))
    async with await _client(app) as c:
        r = await c.get("/governance/policies")
    assert r.status_code == 503, r.text
    assert "deleted-on-another-replica" not in r.text


async def test_no_db_serves_the_in_memory_registry() -> None:
    app = make_app(router)
    app.state.policy_engine = PolicyEngine()
    async with await _client(app) as c:
        created = await c.post(
            "/governance/policies",
            json={"name": "mem", "tools_pattern": "x*", "action": "deny"},
        )
        assert created.status_code == 201, created.text
        r = await c.get("/governance/policies")
    assert r.status_code == 200
    assert [p["name"] for p in r.json()] == ["mem"]


async def test_delete_of_a_policy_missing_from_the_db_is_404() -> None:
    db = _DB()
    app = _with_stale_registry(_app(db))
    async with await _client(app) as c:
        r = await c.delete("/governance/policies/stale-1")
    assert r.status_code == 404, r.text
    assert db.sql("DELETE FROM governance_policies") == []


async def test_delete_when_the_db_list_fails_is_503() -> None:
    db = _DB(fail_on="FROM governance_policies gp")
    app = _with_stale_registry(_app(db))
    async with await _client(app) as c:
        r = await c.delete("/governance/policies/stale-1")
    assert r.status_code == 503, r.text
    assert db.sql("DELETE FROM governance_policies") == []


async def test_policy_stream_snapshot_db_error_is_503() -> None:
    app = _with_stale_registry(_app(_DB(fail_on="FROM governance_policies")))
    async with await _client(app) as c:
        r = await c.get("/governance/policies/stream")
    assert r.status_code == 503, r.text
