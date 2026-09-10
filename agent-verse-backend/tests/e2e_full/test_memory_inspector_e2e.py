"""e2e_full: canonical memory inspector read API + uniform TTL purge on the live path.

Proves the wired memory subsystem against real Postgres:

* Governed memory records of different kinds (episodic / procedural / reflexion),
  written through the DB-backed ``PostgresMemoryRepository`` and linked to a goal,
  come back from ``GET /memory/records`` categorized by ``memory_kind`` with their
  ``source_goal_id`` goal-linkage and per-record ``expires_at`` TTL — filterable by
  kind and by goal, and tenant/RLS scoped.
* The retention purge physically reclaims expired rows of *every* kind uniformly
  (the TTL that ``write()`` now assigns is real, not read-time-only).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.memory.contracts import MemoryWriteRequest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def mem_tenant(app: Any, client: Any) -> AsyncIterator[tuple[str, Any]]:
    """Fresh tenant per test → deterministic counts; yields (tenant_id, http client)."""
    from httpx import ASGITransport, AsyncClient

    email = f"mem-e2e-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Mem E2E", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    tenant_id = resp.json()["tenant_id"]
    api_key = resp.json()["api_key"]

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://e2e-full",
        headers={"X-API-Key": api_key},
    ) as c:
        yield tenant_id, c


def _write(tenant_id: str, kind: str, *, goal: str, policy: str = "default") -> MemoryWriteRequest:
    return MemoryWriteRequest(
        tenant_id=tenant_id,
        memory_kind=kind,  # type: ignore[arg-type]
        content=f"{kind} lesson learned while pursuing {goal}",
        source_goal_id=goal,
        source_execution_id="exec-1",
        evidence_refs=("evidence://1",),
        classification="internal",
        confidence=8000,
        idempotency_key=f"{goal}-{kind}",
        retention_policy_id=policy,
    )


async def test_memory_inspector_returns_categorized_records(
    app: Any, mem_tenant: tuple[str, Any]
) -> None:
    tenant_id, c = mem_tenant
    repo = app.state.memory_repository  # DB-backed PostgresMemoryRepository under manage_pools
    goal = f"goal-{uuid.uuid4().hex[:8]}"

    for kind in ("episodic", "procedural", "reflexion"):
        await repo.write(_write(tenant_id, kind, goal=goal))

    resp = await c.get("/memory/records")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert body["kinds"] == {"episodic": 1, "procedural": 1, "reflexion": 1}
    for record in body["records"]:
        assert record["source_goal_id"] == goal  # goal-linkage
        assert record["expires_at"]  # TTL assigned at write time (real Postgres row)
        assert record["content"]

    # Filter by kind.
    only_proc = await c.get("/memory/records", params={"kind": "procedural"})
    assert [r["memory_kind"] for r in only_proc.json()["records"]] == ["procedural"]

    # Filter by goal-linkage.
    by_goal = await c.get("/memory/records", params={"goal_id": goal})
    assert by_goal.json()["total"] == 3


async def test_ttl_purge_removes_all_kinds_against_postgres(
    app: Any, mem_tenant: tuple[str, Any]
) -> None:
    from datetime import UTC, datetime, timedelta

    tenant_id, c = mem_tenant
    repo = app.state.memory_repository
    goal = f"goal-{uuid.uuid4().hex[:8]}"

    # Three expiring kinds (ephemeral = 1 day TTL) + one permanent survivor.
    for kind in ("episodic", "procedural", "reflexion"):
        await repo.write(_write(tenant_id, kind, goal=goal, policy="ephemeral"))
    await repo.write(
        MemoryWriteRequest(
            tenant_id=tenant_id,
            memory_kind="reflexion",
            content="a permanent lesson",
            source_goal_id=goal,
            source_execution_id="exec-keep",
            evidence_refs=("evidence://1",),
            classification="internal",
            confidence=8000,
            idempotency_key=f"{goal}-keep",
            retention_policy_id="permanent",
        )
    )

    assert (await c.get("/memory/records")).json()["total"] == 4

    future = datetime.now(UTC) + timedelta(days=2)
    deleted = await repo.purge_expired(tenant_id, now=future)
    assert deleted == 3

    after = (await c.get("/memory/records")).json()
    assert after["total"] == 1
    assert after["records"][0]["expires_at"] is None  # only the permanent record remains
