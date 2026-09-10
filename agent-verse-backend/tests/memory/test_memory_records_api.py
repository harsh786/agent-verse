"""GET /memory/records — canonical memory read API for the inspector.

Proves the endpoint surfaces the real memory_kind categorization, source_goal_id
goal-linkage and per-record expires_at that ``GET /memory`` does not, is tenant
scoped, filters by kind + goal, and returns an honest empty list when there are
none.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.memory.contracts import MemoryWriteRequest


def _write(tenant_id: str, kind: str, *, key: str, goal: str) -> MemoryWriteRequest:
    return MemoryWriteRequest(
        tenant_id=tenant_id,
        memory_kind=kind,  # type: ignore[arg-type]
        content=f"{kind} memory for {goal}",
        source_goal_id=goal,
        source_execution_id="exec-1",
        evidence_refs=("evidence://1",),
        classification="internal",
        confidence=8000,
        idempotency_key=key,
        retention_policy_id="default",
    )


@pytest.mark.asyncio
async def test_records_endpoint_categorizes_and_links_goals() -> None:
    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        signup = await client.post("/tenants/signup", json={"name": "Mem", "email": "m@m.com"})
        assert signup.status_code == 201
        tenant_id = signup.json()["tenant_id"]
        client.headers["X-API-Key"] = signup.json()["api_key"]

        repo = app.state.memory_repository
        await repo.write(_write(tenant_id, "episodic", key="e1", goal="goal-a"))
        await repo.write(_write(tenant_id, "procedural", key="p1", goal="goal-a"))
        await repo.write(_write(tenant_id, "reflexion", key="r1", goal="goal-b"))

        # Unfiltered: all three kinds, categorized, with goal-linkage + TTL.
        resp = await client.get("/memory/records")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert body["kinds"] == {"episodic": 1, "procedural": 1, "reflexion": 1}
        by_kind = {r["memory_kind"]: r for r in body["records"]}
        assert by_kind["episodic"]["source_goal_id"] == "goal-a"
        assert by_kind["reflexion"]["source_goal_id"] == "goal-b"
        # TTL exposed and populated (default retention policy → finite deadline).
        assert all(r["expires_at"] for r in body["records"])

        # Filter by kind.
        resp = await client.get("/memory/records", params={"kind": "reflexion"})
        assert resp.status_code == 200
        assert [r["memory_kind"] for r in resp.json()["records"]] == ["reflexion"]

        # Filter by goal-linkage.
        resp = await client.get("/memory/records", params={"goal_id": "goal-a"})
        kinds = sorted(r["memory_kind"] for r in resp.json()["records"])
        assert kinds == ["episodic", "procedural"]

        # Unknown kind → honest 400.
        resp = await client.get("/memory/records", params={"kind": "bogus"})
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_records_endpoint_is_tenant_scoped_and_honest_empty() -> None:
    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        a = await client.post("/tenants/signup", json={"name": "A", "email": "a@a.com"})
        b = await client.post("/tenants/signup", json={"name": "B", "email": "b@b.com"})
        a_tenant, a_key = a.json()["tenant_id"], a.json()["api_key"]
        b_key = b.json()["api_key"]

        await app.state.memory_repository.write(
            _write(a_tenant, "episodic", key="e1", goal="goal-a")
        )

        # Tenant A sees its record.
        client.headers["X-API-Key"] = a_key
        assert (await client.get("/memory/records")).json()["total"] == 1

        # Tenant B sees an honest empty list (no fabrication, no cross-tenant leak).
        client.headers["X-API-Key"] = b_key
        empty = await client.get("/memory/records")
        assert empty.status_code == 200
        assert empty.json() == {"records": [], "total": 0, "kinds": {}}
